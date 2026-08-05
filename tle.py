"""Starlink satellite catalogue download + cache.

Two CelesTrak sources:
- "sup"   -- supplemental GP data fitted from SpaceX's own operator
             ephemerides (more accurate, includes planned manoeuvres).
- "group" -- the standard GROUP=starlink GP set (18th SDS radar-derived).

Default is "sup" with fallback to "group". Files are cached next to this
module in tle_cache/ and refreshed when older than --tle-max-age hours.

Direct-to-cell satellites (name tagged "[DTC]" by CelesTrak) are included
by default: they carry the standard Ku/Ka broadband payload alongside the
cellular one, so they can serve dishes too. arXiv:2601.13790 recommended
filtering them out; pass include_dtc=False to reproduce that. (Checked on
27 logged segments at 62N: no DTC bird ever matched — at that latitude
they top out ~15 deg elevation — so either default gives identical
results there; below ~55N inclusion genuinely matters.)
"""

import logging
import time
import urllib.request
from pathlib import Path

from sgp4.api import Satrec

logger = logging.getLogger(__name__)

CACHE_DIR = Path(__file__).resolve().parent / "tle_cache"

URLS = {
    "sup": "https://celestrak.org/NORAD/elements/supplemental/sup-gp.php?FILE=starlink&FORMAT=tle",
    "group": "https://celestrak.org/NORAD/elements/gp.php?GROUP=starlink&FORMAT=tle",
}


class Satellite:
    """One catalogue entry: name + parsed SGP4 satrec."""

    __slots__ = ("name", "norad", "satrec")

    def __init__(self, name: str, satrec):
        self.name = name
        self.norad = satrec.satnum
        self.satrec = satrec

    def __repr__(self):
        return f"Satellite({self.name}, norad={self.norad})"


def _cache_path(catalog: str) -> Path:
    return CACHE_DIR / f"{catalog}-starlink.tle"


def cache_age_hours(catalog: str):
    path = _cache_path(catalog)
    if not path.exists():
        return None
    return (time.time() - path.stat().st_mtime) / 3600.0


def download(catalog: str) -> Path:
    """Download the given catalogue to the cache, atomically."""
    url = URLS[catalog]
    path = _cache_path(catalog)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    logger.info("Downloading %s catalogue from %s", catalog, url)
    with urllib.request.urlopen(url, timeout=60) as resp:
        data = resp.read()
    if len(data) < 10000 or b"STARLINK" not in data[:2000]:
        raise RuntimeError(f"TLE download from {url} looks wrong "
                           f"({len(data)} bytes); not overwriting cache")
    tmp.write_bytes(data)
    tmp.replace(path)
    return path


def parse_tle_file(path: Path, include_dtc: bool = True):
    """Parse a 3-line-element file into Satellite objects."""
    sats = []
    lines = path.read_text().splitlines()
    for i in range(0, len(lines) - 2, 3):
        name, l1, l2 = lines[i].strip(), lines[i + 1], lines[i + 2]
        if not l1.startswith("1 ") or not l2.startswith("2 "):
            raise ValueError(f"malformed TLE triplet at line {i + 1} of {path}")
        if not include_dtc and "[DTC]" in name:
            continue
        sats.append(Satellite(name, Satrec.twoline2rv(l1, l2)))
    return sats


def load_catalogue(catalog: str = "sup", max_age_hours: float = 12.0,
                   include_dtc: bool = True, offline: bool = False):
    """Return (list[Satellite], catalog_used, age_hours), refreshing the cache
    when stale. Falls back to the other source, then to a stale cache."""
    order = [catalog, "group" if catalog == "sup" else "sup"]
    last_err = None
    for cat in order:
        age = cache_age_hours(cat)
        if not offline and (age is None or age > max_age_hours):
            try:
                download(cat)
                age = 0.0
            except Exception as e:
                last_err = e
                logger.warning("Download of %s catalogue failed: %s", cat, e)
                if age is None:
                    continue
                logger.warning("Using stale %s cache (%.1f h old)", cat, age)
        if age is not None:
            sats = parse_tle_file(_cache_path(cat), include_dtc=include_dtc)
            logger.info("Loaded %d satellites from %s catalogue (%.1f h old, DTC %s)",
                        len(sats), cat, age, "included" if include_dtc else "excluded")
            return sats, cat, age
    raise RuntimeError(f"No usable TLE catalogue (last error: {last_err})")
