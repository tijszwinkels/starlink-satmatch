"""Per-satellite background info from the CelesTrak SATCAT + current TLEs.

SATCAT gives launch date/site, decay date and an operational-status code;
the current TLE gives a fresher orbit (the SATCAT orbit columns can lag).
Starlink hardware version is NOT in any public catalogue, so it is inferred
from the launch date (clearly labelled as a heuristic).
"""

import csv
import logging
import math
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

from tle import CACHE_DIR, download_atomic, file_age_hours

logger = logging.getLogger(__name__)

SATCAT_URL = "https://celestrak.org/pub/satcat.csv"
SATCAT_PATH = CACHE_DIR / "satcat.csv"
SATCAT_MAX_AGE_HOURS = 7 * 24.0

MU_KM3_S2 = 398600.4418
EARTH_RADIUS_KM = 6371.0

OPS_STATUS = {
    "+": "Operational",
    "P": "Partially operational",
    "-": "Nonoperational",
    "B": "Backup/standby",
    "S": "Spare",
    "X": "Extended mission",
    "D": "Decayed",
    "?": "Unknown",
    "": "Unknown",
}

LAUNCH_SITES = {
    "AFETR": "Cape Canaveral / Kennedy (Eastern Range)",
    "AFWTR": "Vandenberg SFB (Western Range)",
}

# First-launch dates of each hardware generation. Public catalogues don't
# carry the version, so this is a heuristic on launch date + shell.
VERSION_EPOCHS = [
    (date(2023, 2, 27), "V2 Mini family (Gen2)"),
    (date(2021, 6, 1), "v1.5 (Gen1)"),
    (date(2019, 11, 1), "v1.0 (Gen1)"),
    (date(1900, 1, 1), "v0.9 (prototype)"),
]

# The Gen1 70 deg and 97.6 deg shells kept receiving v1.5 hardware well
# after the first V2 Mini launch (Groups 2/3 from Vandenberg through 2023).
GEN1_LATE_SHELLS = ((68.5, 71.5), (96.0, 99.0))
GEN1_LATE_CUTOFF = date(2024, 1, 1)


@dataclass
class SatcatEntry:
    name: str
    intl_designator: str
    norad: int
    ops_status_code: str
    launch_date: str      # YYYY-MM-DD
    launch_site: str
    decay_date: str
    period_min: float
    inclination_deg: float
    apogee_km: float
    perigee_km: float


def _fetch_satcat():
    download_atomic(SATCAT_URL, SATCAT_PATH, min_bytes=1_000_000,
                    head_check=lambda d: d.startswith(b"OBJECT_NAME,"),
                    timeout=120.0)


def load_satcat(max_age_hours=SATCAT_MAX_AGE_HOURS, offline=False):
    """dict: NORAD id -> SatcatEntry for all Starlink objects."""
    age = file_age_hours(SATCAT_PATH)
    if not offline and (age is None or age > max_age_hours):
        try:
            _fetch_satcat()
        except Exception as e:
            if age is None:
                raise RuntimeError(
                    f"no cached SATCAT and the download failed: {e}") from e
            logger.warning("SATCAT refresh failed, using %.0f h old cache: %s",
                           age, e)
    if not SATCAT_PATH.exists():
        raise RuntimeError(f"no SATCAT cached at {SATCAT_PATH}; "
                           "run once without --offline to download it")

    out = {}
    with open(SATCAT_PATH, newline="") as fh:
        for row in csv.DictReader(fh):
            if not row["OBJECT_NAME"].startswith("STARLINK"):
                continue
            out[int(row["NORAD_CAT_ID"])] = SatcatEntry(
                name=row["OBJECT_NAME"],
                intl_designator=row["OBJECT_ID"],
                norad=int(row["NORAD_CAT_ID"]),
                ops_status_code=row["OPS_STATUS_CODE"].strip(),
                launch_date=row["LAUNCH_DATE"],
                launch_site=row["LAUNCH_SITE"],
                decay_date=row["DECAY_DATE"],
                period_min=float(row["PERIOD"] or 0),
                inclination_deg=float(row["INCLINATION"] or 0),
                apogee_km=float(row["APOGEE"] or 0),
                perigee_km=float(row["PERIGEE"] or 0),
            )
    return out


def starlink_version(launch_date_iso, name="", incl_deg=None):
    """Heuristic hardware generation from launch date + orbital shell."""
    tag = " · direct-to-cell" if "[DTC]" in name else ""
    try:
        launched = date.fromisoformat(launch_date_iso)
    except ValueError:
        return "unknown" + tag
    if (incl_deg is not None and launched < GEN1_LATE_CUTOFF
            and any(lo <= incl_deg <= hi for lo, hi in GEN1_LATE_SHELLS)
            and launched >= date(2021, 6, 1)):
        return "v1.5 (Gen1)" + tag
    for epoch, label in VERSION_EPOCHS:
        if launched >= epoch:
            return label + tag
    return "unknown" + tag


def age_years(launch_date_iso, today=None):
    launched = date.fromisoformat(launch_date_iso)
    return ((today or date.today()) - launched).days / 365.25


def orbit_from_satrec(satrec):
    """(perigee_km, apogee_km, inclination_deg, period_min) from the
    current TLE mean elements."""
    n_rad_s = satrec.no_kozai / 60.0
    a_km = (MU_KM3_S2 / (n_rad_s ** 2)) ** (1.0 / 3.0)
    e = satrec.ecco
    return (a_km * (1 - e) - EARTH_RADIUS_KM,
            a_km * (1 + e) - EARTH_RADIUS_KM,
            math.degrees(satrec.inclo),
            2.0 * math.pi / satrec.no_kozai)


def format_info(sat, entry, in_ephemerides_feed):
    """Multiline info block for one satellite.

    sat: tle.Satellite (current TLE); entry: SatcatEntry or None;
    in_ephemerides_feed: present in the SpaceX supplemental catalogue.
    """
    lines = [f"{sat.name} · NORAD {sat.norad}"
             + (f" · Intl {entry.intl_designator}" if entry else "")]
    if entry is None:
        lines.append("    no SATCAT entry (very recent launch, or stale "
                     "satcat.csv cache — try tle --refresh)")
    else:
        version = starlink_version(entry.launch_date, sat.name,
                                   math.degrees(sat.satrec.inclo))
        site = LAUNCH_SITES.get(entry.launch_site, entry.launch_site)
        lines.append(f"    type:     {version}  [heuristic from launch date]")
        lines.append(f"    launched: {entry.launch_date} from {site} "
                     f"· age {age_years(entry.launch_date):.1f} years")
        status = OPS_STATUS.get(entry.ops_status_code,
                                f"code {entry.ops_status_code!r}")
        if entry.decay_date:
            status += f" · decayed {entry.decay_date}"
        feed = ("in SpaceX ephemerides feed"
                if in_ephemerides_feed else "NOT in SpaceX ephemerides feed")
        lines.append(f"    status:   {status} (SATCAT "
                     f"'{entry.ops_status_code or '?'}') · {feed}")
    peri, apo, incl, period = orbit_from_satrec(sat.satrec)
    epoch_days = (datetime.now(timezone.utc) - _satrec_epoch(sat.satrec)).days
    lines.append(f"    orbit:    {peri:.0f} × {apo:.0f} km · incl {incl:.1f}° "
                 f"· period {period:.1f} min  [TLE {epoch_days} d old]")
    return "\n".join(lines)


def _satrec_epoch(satrec):
    jd = satrec.jdsatepoch + satrec.jdsatepochF
    return (datetime(2000, 1, 1, 12, tzinfo=timezone.utc)
            + timedelta(days=jd - 2451545.0))
