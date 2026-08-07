#!/usr/bin/env python3
"""Serve the visualization and keep satellites.json fresh.

    python3 visualization/serve.py [--port 8642] [--bind 127.0.0.1]
                                   [--max-age-hours 24] [--offline]

On startup, and then every 30 minutes in the background, the exporter
re-runs whenever satellites.json is missing or older than --max-age-hours
(the export itself follows satmatch's cache policy: TLEs refresh past 12 h,
SATCAT past 7 days). History and the current satellite reach the page live
regardless — the periodic export covers the catalogue side: new launches,
drifted TLEs, status changes. Reload the page to pick up a fresh export.

Binds to localhost by default since the data includes your dish location
and usage history; pass --bind 0.0.0.0 to expose it on the LAN.
"""

import argparse
import functools
import http.server
import logging
import sys
import threading
import time
from pathlib import Path

VIZ = Path(__file__).resolve().parent
sys.path.insert(0, str(VIZ))
import export as exporter

OUT = VIZ / "satellites.json"
CHECK_INTERVAL_S = 30 * 60


def age_hours():
    if not OUT.exists():
        return None
    return (time.time() - OUT.stat().st_mtime) / 3600.0


def refresh_if_stale(max_age_h, offline):
    age = age_hours()
    if age is not None and age <= max_age_h:
        return
    logging.info("satellites.json %s — running export",
                 "missing" if age is None else f"is {age:.1f} h old")
    try:
        exporter.export(OUT, offline=offline)
    except Exception as e:
        logging.error("export failed, serving stale data: %s", e)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", type=int, default=8642)
    ap.add_argument("--bind", default="127.0.0.1",
                    help="interface to bind (default localhost only)")
    ap.add_argument("--max-age-hours", type=float, default=24.0,
                    help="re-export when satellites.json is older than this")
    ap.add_argument("--offline", action="store_true",
                    help="never download catalogues; use caches as-is")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")

    refresh_if_stale(args.max_age_hours, args.offline)

    def freshness_loop():
        while True:
            time.sleep(CHECK_INTERVAL_S)
            refresh_if_stale(args.max_age_hours, args.offline)

    threading.Thread(target=freshness_loop, daemon=True).start()

    handler = functools.partial(http.server.SimpleHTTPRequestHandler,
                                directory=str(VIZ))
    server = http.server.ThreadingHTTPServer((args.bind, args.port), handler)
    logging.info("serving http://%s:%d  (export refresh > %g h, checked every %d min)",
                 args.bind, args.port, args.max_age_hours, CHECK_INTERVAL_S // 60)
    server.serve_forever()


if __name__ == "__main__":
    main()
