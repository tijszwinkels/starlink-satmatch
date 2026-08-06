#!/usr/bin/env python3
"""Export the satellite catalogue + personal history to satellites.json.

Merges four sources into one flat record per satellite:
  - TLE catalogue (tle_cache/, via tle.load_catalogue): name, orbit, raw TLE
  - SATCAT (satinfo.load_satcat): launch date/site, status, intl designator
  - sat_history.json: personal dwells/slots/seconds/bytes/last_seen
  - type heuristic (satinfo.starlink_version)

Run from anywhere:  python3 visualization/export.py [-o out.json] [--offline]
"""

import argparse
import json
import logging
import math
import sys
from datetime import datetime, timezone
from pathlib import Path

SATMATCH = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SATMATCH))

import satinfo
import tle


def load_raw_tle_lines(catalog_used):
    """NORAD id -> (line1, line2) from the cached 3LE file."""
    path = tle.CACHE_DIR / f"{catalog_used}-starlink.tle"
    out = {}
    lines = path.read_text().splitlines()
    for i in range(0, len(lines) - 2, 3):
        l1, l2 = lines[i + 1], lines[i + 2]
        out[int(l1[2:7])] = (l1.rstrip(), l2.rstrip())
    return out


def load_history():
    path = SATMATCH / "sat_history.json"
    if not path.exists():
        logging.warning("no sat_history.json — exporting catalogue only")
        return {}
    return json.loads(path.read_text())


def export(out_path, offline=False):
    sats, catalog_used, age_h = tle.load_catalogue(offline=offline)
    satcat = satinfo.load_satcat(offline=offline)
    raw_tle = load_raw_tle_lines(catalog_used)
    history = load_history()

    last_connected = None
    if history:
        last_connected = max(history.items(), key=lambda kv: kv[1]["last_seen"])

    records = []
    for sat in sats:
        peri, apo, incl, period = satinfo.orbit_from_satrec(sat.satrec)
        entry = satcat.get(sat.norad)
        hist = history.get(str(sat.norad))
        dtc = "[DTC]" in sat.name
        name = sat.name.replace("[DTC]", "").strip()
        sat_type = None
        if entry:
            sat_type = satinfo.starlink_version(
                entry.launch_date, sat.name, incl).replace(" · direct-to-cell", "")
        records.append({
            "norad": sat.norad,
            "name": name,
            "intl": entry.intl_designator if entry else None,
            "type": sat_type,
            "dtc": dtc,
            "launch_date": entry.launch_date if entry else None,
            "launch_site": satinfo.LAUNCH_SITES.get(
                entry.launch_site, entry.launch_site) if entry else None,
            "status": satinfo.OPS_STATUS.get(
                entry.ops_status_code, entry.ops_status_code) if entry else None,
            "incl_deg": round(incl, 2),
            "alt_km": round((peri + apo) / 2),
            "perigee_km": round(peri),
            "apogee_km": round(apo),
            "period_min": round(period, 1),
            "dwells": hist["dwells"] if hist else 0,
            "slots": hist["slots"] if hist else 0,
            "seconds": round(hist.get("seconds", 0), 1) if hist else 0,
            "down_bytes": round(hist["down_bytes"]) if hist else 0,
            "up_bytes": round(hist["up_bytes"]) if hist else 0,
            "last_seen": hist["last_seen"] if hist else None,
            "tle": list(raw_tle.get(sat.norad, ())) or None,
        })

    doc = {
        "generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "catalog": catalog_used,
        "catalog_age_hours": round(age_h, 1),
        "last_connected_norad": int(last_connected[0]) if last_connected else None,
        "count": len(records),
        "connected_count": sum(1 for r in records if r["dwells"]),
        "satellites": records,
    }
    tmp = out_path.with_suffix(".tmp")
    tmp.write_text(json.dumps(doc, separators=(",", ":")))
    tmp.replace(out_path)
    logging.info("wrote %s: %d satellites (%d with history), last connected %s",
                 out_path, doc["count"], doc["connected_count"],
                 last_connected[1]["name"] if last_connected else "n/a")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("-o", "--out", type=Path,
                    default=Path(__file__).parent / "satellites.json")
    ap.add_argument("--offline", action="store_true",
                    help="never download; use caches as-is")
    args = ap.parse_args()
    export(args.out, offline=args.offline)
