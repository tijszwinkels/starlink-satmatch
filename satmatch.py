#!/usr/bin/env python3
"""satmatch -- estimate which Starlink satellite the dish is paired to.

The dish never reports its serving satellite, but its obstruction map records
which sky directions the phased array actually used. Within one 15 s
scheduling slot (slot boundaries at :12/:27/:42/:57 UTC, globally
synchronized) we reset the map, watch which pixels light up second by
second, convert them to az/el, and match the resulting sky track against
SGP4 propagation of the full Starlink catalogue (CelesTrak).

WARNING: identify mode clears the dish's obstruction map each slot, which
discards its learned obstruction history until it re-learns it. Fine for
experiments, not great on a link you depend on.

Usage examples:
    satmatch.py identify --location 59.91,10.75
    satmatch.py identify --slots 4 --log slots.jsonl
    satmatch.py fov
    satmatch.py tle --refresh

Location is taken from --location, else the dish (needs "allow access on
local network" enabled in the Starlink app), else location.json — written
automatically by `locate`, or by --save-location.
"""

import argparse
import json
import logging
import math
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from dishy import Dish
from geometry import FRAME_EARTH, FRAME_UT, angular_separation_deg
from matcher import (MIN_ELEVATION_DEG, annotate_azel, extract_trail,
                     score_segment, segment_trail)
from propagation import batch_altaz, parse_latlon
from tle import load_catalogue

logger = logging.getLogger("satmatch")

LOCATION_FILE = Path(__file__).resolve().parent / "location.json"
SLOT_PERIOD = 15
SLOT_OFFSET = 12          # slot boundaries at :12/:27/:42/:57 UTC
SAMPLE_WINDOW = 13.5      # stop sampling this long after the boundary
CONFIDENT_P = 0.75
CONFIDENT_EPS = 3.0       # a lone bad match is not a confident match
MAX_SLOT_FAILURES = 40    # consecutive gRPC failures before giving up (~10 min)
MAX_DWELL_GAP_S = 90.0    # unconfirmed gap that force-closes a dwell: system
                          # sleep or outage, not a normal idle stretch


def is_confident(c):
    return c.eps_deg <= CONFIDENT_EPS and c.likelihood >= CONFIDENT_P


def resolve_location(args, dish):
    if args.location:
        loc = parse_latlon(args.location)
        source = "command line"
    else:
        loc = dish.try_get_location() if dish else None
        source = "dish GPS"
        if loc is None and LOCATION_FILE.exists():
            data = json.loads(LOCATION_FILE.read_text())
            loc = (data["lat"], data["lon"], data.get("alt_m", 0.0))
            source = f"saved file ({LOCATION_FILE.name})"
    if loc is None:
        sys.exit(
            "No observer location available. Either:\n"
            "  - pass --location 'lat,lon[,alt_m]', or\n"
            "  - enable location over LAN in the Starlink app\n"
            "    (Settings -> Advanced -> Debug data -> allow access on local network)\n"
            "Matching needs the position to ~10 km; it is only used locally.")
    if args.save_location:
        LOCATION_FILE.write_text(json.dumps(
            {"lat": loc[0], "lon": loc[1], "alt_m": loc[2]}))
        print(f"Saved location to {LOCATION_FILE}")
    print(f"Observer: {loc[0]:.4f}, {loc[1]:.4f}, {loc[2]:.0f} m  [{source}]")
    return loc


def slot_boundary_before(t):
    """The :12/:27/:42/:57 UTC boundary at or before unix time t."""
    return t - ((t - SLOT_OFFSET) % SLOT_PERIOD)


def nearest_slot_boundary(t):
    return SLOT_OFFSET + SLOT_PERIOD * round((t - SLOT_OFFSET) / SLOT_PERIOD)


def slot_index(t):
    return int((t - SLOT_OFFSET) // SLOT_PERIOD)


def wait_for_slot_boundary():
    """Sleep until just after the next :12/:27/:42/:57 UTC boundary.
    Returns the boundary's unix time."""
    now = time.time()
    into = (now - SLOT_OFFSET) % SLOT_PERIOD
    boundary = now + (SLOT_PERIOD - into)
    time.sleep(boundary + 0.2 - now)
    return boundary


def collect_slot(dish, boundary, hz=2.0):
    """Reset the map at the slot boundary and sample it at `hz` for the slot.
    (The map itself seems to update ~1 Hz; oversampling tightens the
    timestamp attached to each newly lit pixel.)"""
    dish.reset_map()
    samples = [dish.get_map()]
    step = 1.0 / hz
    t_end = boundary + SAMPLE_WINDOW
    while True:
        next_t = math.floor(time.time() / step + 1.0) * step
        if next_t > t_end:
            break
        time.sleep(max(0.0, next_t - time.time()))
        try:
            samples.append(dish.get_map())
        except Exception as e:
            logger.warning("map read failed mid-slot: %s", e)
    return samples


def fmt_bytes(n):
    for div, unit in ((1e9, "GB"), (1e6, "MB"), (1e3, "kB")):
        if n >= div:
            return f"{n / div:.1f} {unit}"
    return f"{n:.0f} B"


def fmt_rate(bps):
    for div, unit in ((1e6, "Mbit/s"), (1e3, "kbit/s")):
        if bps >= div:
            return f"{bps / div:.1f} {unit}"
    return f"{bps:.0f} bit/s"


def fmt_duration(s):
    s = int(round(s))
    if s < 60:
        return f"{s} s"
    if s < 3600:
        return f"{s // 60} m {s % 60:02d} s"
    return f"{s // 3600} h {s % 3600 // 60:02d} m"


class DwellLog:
    """--dwells mode: print only serving-satellite changes.

    A dwell opens when a satellite is confidently identified and closes when
    a *different* satellite is confidently identified (or at exit). Slots
    with no confident ID neither open nor close anything — on a sparse/idle
    link the same satellite usually continues — but the close line reports
    confirmed/elapsed slots so silent gaps stay visible.

    Data transferred per dwell is integrated from the dish's 1 Hz history
    ring over the dwell window. Windows are contiguous and slot-aligned:
    a dwell starts at the boundary of its first confirmed slot, and a
    handover cut snaps to the slot boundary nearest the midpoint between
    the last evidence of the old satellite and the first evidence of the
    new one — handovers happen on the 15 s grid. The exception is when both
    satellites were seen within the *same* slot (a demonstrable intra-slot
    beam switch): there the midpoint itself is kept. At exit the open dwell
    closes at the end of its last confirmed slot (or now, if earlier).
    """

    def __init__(self, satcat, by_norad, dish=None, history=None, log_fh=None,
                 current_path=None):
        self.satcat = satcat
        self.by_norad = by_norad   # reassigned on mid-run TLE refresh
        self.dish = dish
        self.history = history     # SatHistory, or None to skip the tally
        self.log_fh = log_fh       # append-only JSONL, one record per dwell
        self.current_path = current_path  # live current-satellite file, or None
        self.cur = None

    def _publish_current(self):
        if self.current_path is None or self.cur is None:
            return
        try:
            from history import write_current
            write_current(self.cur["norad"], self.cur["name"],
                          self.cur["start"], self.current_path)
        except OSError as e:
            logger.warning("current-satellite file write failed: %s", e)

    @staticmethod
    def _stamp(t):
        return datetime.fromtimestamp(t, tz=timezone.utc).strftime(
            "%Y-%m-%d %H:%M:%S")

    def observe(self, segments):
        for seg in segments:
            if not seg.candidates:
                continue
            c = seg.candidates[0]
            if not is_confident(c):
                continue
            if self.cur is not None:
                gap = seg.t_start - self.cur["last_seen"]
                if gap > MAX_DWELL_GAP_S:
                    # laptop sleep / outage: close honestly at the end of the
                    # last confirmed slot; never bridge the gap — not even
                    # for the same satellite (it may have come back around)
                    self.close()
                    print(f"(no confident ID for {fmt_duration(gap)} — "
                          "system sleep or outage; dwell closed)")
            if self.cur and c.norad == self.cur["norad"]:
                self.cur["last_seen"] = seg.t_end
                self.cur["slot_set"].add(slot_index(seg.t_start))
                self.cur["n_segs"] += 1
                self.cur["eps_sum"] += c.eps_deg
                self._publish_current()   # refresh the "updated" stamp
            else:
                cut = None
                if self.cur is not None:
                    mid = (self.cur["last_seen"] + seg.t_start) / 2.0
                    if slot_index(self.cur["last_seen"]) == slot_index(seg.t_start):
                        cut = mid            # intra-slot beam switch: the old
                        self.cur["elapsed"] += 1  # dwell was active this slot,
                        # which the end-of-pass tick won't count for it
                    else:
                        cut = nearest_slot_boundary(mid)
                    self.close(end_t=cut)
                self.cur = {"norad": c.norad, "name": c.name,
                            "start": seg.t_start, "last_seen": seg.t_end,
                            "win_start": (cut if cut is not None
                                          else slot_boundary_before(seg.t_start)),
                            "slot_set": {slot_index(seg.t_start)},
                            "n_segs": 1, "elapsed": 0,
                            "eps_sum": c.eps_deg}
                self._publish_current()
                self._print_open(c)
        if self.cur is not None:
            self.cur["elapsed"] += 1

    def _print_open(self, c):
        print(f"▶ {self._stamp(self.cur['start'])} UTC — "
              f"{self.cur['name']} (NORAD {self.cur['norad']}) "
              f"ε={c.eps_deg:.1f}° · el {c.el_deg:.0f}° az {c.az_deg:.0f}° "
              f"· {c.range_km:.0f} km")
        sat = self.by_norad.get(self.cur["norad"])
        if sat is not None and self.satcat is not None:  # --satellite-info
            import satinfo
            print("  " + satinfo.format_info(
                sat, self.satcat.get(sat.norad), True).replace("\n", "\n  "))

    def _measure_transfer(self, win_start, end_t):
        """(down_bytes, up_bytes, is_lower_bound) for the window, or None
        when the dish's throughput history is unavailable."""
        if self.dish is None:
            return None
        try:
            need = time.time() - win_start + 5.0
            ts, down, up = self.dish.get_history_throughput(need)
        except Exception as e:
            logger.warning("throughput history unavailable: %s", e)
            return None
        dn = sum(b for t, b in zip(ts, down) if win_start <= t <= end_t) / 8.0
        ub = sum(b for t, b in zip(ts, up) if win_start <= t <= end_t) / 8.0
        # ring didn't reach back to the window start -> lower bound
        return dn, ub, bool(ts) and ts[0] > win_start + 1.5

    def close(self, end_t=None):
        if self.cur is None:
            return
        d = self.cur
        self.cur = None
        if end_t is None:
            # exit: the pairing held through the last confirmed slot
            end_t = min(slot_boundary_before(d["last_seen"]) + SLOT_PERIOD,
                        time.time())
        dur = max(end_t - d["win_start"], 1.0)
        xfer = self._measure_transfer(d["win_start"], end_t)
        lead = ""
        if xfer is not None:
            dn, ub, approx = xfer
            a = "≥" if approx else ""
            lead = (f"↓ {a}{fmt_bytes(dn)} ({fmt_rate(dn * 8.0 / dur)}) "
                    f"↑ {a}{fmt_bytes(ub)} ({fmt_rate(ub * 8.0 / dur)}) · ")
        n_conf = len(d["slot_set"])
        mean_eps = d["eps_sum"] / d["n_segs"]
        print(f"■ {self._stamp(end_t)} UTC · {lead}tracked {dur:.0f} s · "
              f"confirmed in {n_conf}/{d['elapsed']} slot(s) · "
              f"mean ε {mean_eps:.1f}°")
        if self.log_fh is not None:
            def iso(t):
                return datetime.fromtimestamp(t, tz=timezone.utc).isoformat(
                    timespec="milliseconds")
            dn, ub, approx = xfer if xfer is not None else (None, None, None)
            self.log_fh.write(json.dumps({
                "start": iso(d["win_start"]), "end": iso(end_t),
                "seconds": round(dur, 1),
                "name": d["name"], "norad": d["norad"],
                "slots_confirmed": n_conf,
                "slots_elapsed": d["elapsed"],
                "segments": d["n_segs"],
                "mean_eps_deg": round(mean_eps, 2),
                "first_evidence": iso(d["start"]),
                "last_evidence": iso(d["last_seen"]),
                "down_bytes": round(dn) if dn is not None else None,
                "up_bytes": round(ub) if ub is not None else None,
                "transfer_lower_bound": approx,
            }) + "\n")
            self.log_fh.flush()
        if self.history is not None:
            dn, ub = (xfer[0], xfer[1]) if xfer is not None else (0.0, 0.0)
            e = self.history.record_dwell(
                d["norad"], d["name"], n_conf, dn, ub, seconds=dur,
                when=datetime.fromtimestamp(end_t, tz=timezone.utc))
            print(f"  ∑ all-time with this satellite: "
                  f"{e['dwells']} dwell{'s' if e['dwells'] != 1 else ''} · "
                  f"{e['slots']} slot{'s' if e['slots'] != 1 else ''} · "
                  f"{fmt_duration(e['seconds'])} · "
                  f"↓ {fmt_bytes(e['down_bytes'])} ↑ {fmt_bytes(e['up_bytes'])}")
        print()


def print_slot_result(slot_idx, boundary, points, segments):
    stamp = datetime.fromtimestamp(boundary, tz=timezone.utc).strftime("%H:%M:%S")
    print(f"\n─ slot {slot_idx} @ {stamp} UTC · {len(points)} trail points · "
          f"{len(segments)} segment(s)")
    for seg in segments:
        if len(segments) > 1:
            t0 = datetime.fromtimestamp(seg.t_start, tz=timezone.utc)
            print(f"  segment {t0.strftime('%H:%M:%S')} "
                  f"({len(seg.points)} pts):")
        if not seg.candidates:
            print("    no catalogue satellite matches this track "
                  f"(min {len(seg.points)} points needed: 3)")
            continue
        for rank, c in enumerate(seg.candidates[:5], 1):
            marker = ""
            if rank == 1:
                marker = (" ◀ confident" if is_confident(c)
                          else " ◀ weak match (high ε)" if c.likelihood >= CONFIDENT_P
                          else "")
            print(f"    #{rank} {c.name:<17} NORAD {c.norad:<6} "
                  f"ε={c.eps_deg:4.1f}°  p={c.likelihood:4.2f}  "
                  f"el {c.el_deg:4.1f}° az {c.az_deg:5.1f}°  "
                  f"{c.range_km:4.0f} km{marker}")


def log_slot(fh, boundary, dish_state, points, segments):
    if fh is None:
        return
    rec = {
        "slot_start": datetime.fromtimestamp(boundary, tz=timezone.utc).isoformat(),
        "dish": vars(dish_state),
        "points": [{"t": p.t, "x": p.x, "y": p.y, "n_new": p.n_new,
                    "el": p.el, "az": p.az} for p in points],
        "segments": [{
            "t_start": s.t_start, "t_end": s.t_end,
            "candidates": [vars(c) for c in s.candidates[:10]],
        } for s in segments],
    }
    fh.write(json.dumps(rec) + "\n")
    fh.flush()


def cmd_identify(args):
    sats, catalog, age = load_catalogue(args.catalog, args.tle_max_age,
                                        offline=args.offline)
    print(f"Catalogue: {len(sats)} satellites ({catalog}, {age:.1f} h old)")
    by_norad = {s.norad: s for s in sats}
    tle_loaded = time.time()

    satcat = None
    if args.satellite_info:
        import satinfo
        satcat = satinfo.load_satcat(offline=args.offline)

    if args.log_dwells:
        args.dwells = True
    dwell_log = None
    dwell_fh = None
    if args.dwells:
        from history import SatHistory, CURRENT_PATH
        if args.log_dwells:
            dwell_fh = open(args.log_dwells, "a")
        dwell_log = DwellLog(satcat, by_norad, dish=None,  # dish set below
                             history=SatHistory(), log_fh=dwell_fh,
                             current_path=CURRENT_PATH)
        if not args.slots:
            args.watch = True

    dish = Dish(target=args.target)
    if dwell_log is not None:
        dwell_log.dish = dish
    try:
        state = dish.get_state()
    except Exception as e:
        sys.exit(f"cannot reach the dish at "
                 f"{args.target or '192.168.100.1:9200'}: {e}")
    print(f"Dish: {state.hardware} · {state.state} · "
          f"boresight az {state.boresight_az_deg:.1f}° el {state.boresight_el_deg:.1f}° "
          f"(tilt {state.tilt_deg:.1f}°) · attitude {state.attitude_state} "
          f"±{state.attitude_uncertainty_deg:.2f}°")
    if state.state != "CONNECTED":
        print(f"WARNING: dish state is {state.state}; matching may be meaningless")
    if state.attitude_state != "FILTER_CONVERGED" or state.attitude_uncertainty_deg > 2.0:
        print("WARNING: attitude estimate is poor; FRAME_UT geometry will be off")

    lat, lon, alt_m = resolve_location(args, dish)

    frame = dish.get_map().frame
    frame_name = {FRAME_EARTH: "FRAME_EARTH", FRAME_UT: "FRAME_UT"}.get(frame, str(frame))
    print(f"Map frame: {frame_name}")
    print("NOTE: each slot resets the dish's obstruction map "
          "(learned obstruction history is discarded).")

    log_fh = open(args.log, "a") if args.log else None
    tally = {}   # name -> [wins, sum_eps, sum_p]
    info_shown = set()
    slot_idx = 0
    grpc_failures = 0   # consecutive; dish reboots (fw updates) last minutes
    try:
        while True:
            try:
                # long --watch runs: pick up fresh TLEs between slots
                if (not args.offline
                        and time.time() - tle_loaded > args.tle_max_age * 3600):
                    sats, catalog, age = load_catalogue(
                        args.catalog, args.tle_max_age, offline=args.offline)
                    by_norad = {s.norad: s for s in sats}
                    if dwell_log is not None:
                        dwell_log.by_norad = by_norad
                    tle_loaded = time.time()
                    print(f"(catalogue refreshed: {len(sats)} satellites, "
                          f"{age:.1f} h old)")
                boundary = wait_for_slot_boundary()
                state = dish.get_state()
                samples = collect_slot(dish, boundary)
                slot_idx += 1
                points = extract_trail(samples)
                annotate_azel(points, frame, samples[0].geom, state)
                segments = [score_segment(s, sats, lat, lon, alt_m)
                            for s in segment_trail(points)]
                if dwell_log is not None:
                    dwell_log.observe(segments)
                else:
                    print_slot_result(slot_idx, boundary, points, segments)
                log_slot(log_fh, boundary, state, points, segments)
                grpc_failures = 0
            except Exception as e:
                # survive dish reboots / transient gRPC errors in --watch
                grpc_failures += 1
                logger.warning("slot failed (%d consecutive): %s",
                               grpc_failures, e)
                if grpc_failures >= MAX_SLOT_FAILURES:
                    print(f"\nGiving up after {grpc_failures} consecutive "
                          "failures (~10 min) — dish unreachable?")
                    break
                time.sleep(2.0)
                continue

            slot_confident = False
            for seg in segments:
                if seg.candidates:
                    c = seg.candidates[0]
                    t = tally.setdefault(c.name, [0, 0.0, 0.0])
                    t[0] += 1
                    t[1] += c.eps_deg
                    t[2] += c.likelihood
                    slot_confident = slot_confident or is_confident(c)
                    if (satcat is not None and dwell_log is None
                            and c.norad not in info_shown
                            and c.norad in by_norad):
                        info_shown.add(c.norad)
                        import satinfo
                        print("  " + satinfo.format_info(
                            by_norad[c.norad], satcat.get(c.norad),
                            True).replace("\n", "\n  "))

            if args.watch:
                continue
            if args.slots:
                if slot_idx >= args.slots:
                    break
            elif slot_idx >= args.min_slots and slot_confident:
                break
            elif slot_idx >= args.max_slots:
                print(f"\nStill ambiguous after {slot_idx} slots; stopping "
                      "(TLE staleness or near-collinear satellites?)")
                break
    except KeyboardInterrupt:
        if dwell_log is not None:
            dwell_log.close()
        print("\nInterrupted.")
    finally:
        if dwell_log is not None:
            dwell_log.close()
        if dwell_fh:
            dwell_fh.close()
        if log_fh:
            log_fh.close()
        dish.close()

    if tally:
        print("\n═ summary ═")
        for name, (wins, s_eps, s_p) in sorted(tally.items(),
                                               key=lambda kv: -kv[1][0]):
            print(f"  {name:<17} won {wins} slot(s) · mean ε {s_eps / wins:.1f}° "
                  f"· mean p {s_p / wins:.2f}")


def cmd_info(args):
    """Standalone lookup: satmatch.py info STARLINK-5539 55747 ..."""
    import satinfo
    sats, _, _ = load_catalogue(args.catalog, args.tle_max_age,
                                offline=args.offline)
    by_name = {s.name.replace(" [DTC]", ""): s for s in sats}
    by_norad = {s.norad: s for s in sats}
    satcat = satinfo.load_satcat(offline=args.offline)
    for query in args.satellites:
        sat = (by_norad.get(int(query)) if query.isdigit()
               else by_name.get(query.upper()))
        if sat is None:
            print(f"{query}: not in the current catalogue")
        else:
            print(satinfo.format_info(sat, satcat.get(sat.norad), True))


def cmd_fov(args):
    """List catalogue satellites currently above the horizon near the dish
    FOV, without touching the obstruction map."""
    sats, catalog, age = load_catalogue(args.catalog, args.tle_max_age,
                                        offline=args.offline)
    dish = Dish(target=args.target)
    state = dish.get_state()
    lat, lon, alt_m = resolve_location(args, dish)
    dish.close()

    now = datetime.now(timezone.utc)
    el, az, rng, ok = batch_altaz([s.satrec for s in sats], [now], lat, lon, alt_m)
    el, az, rng, ok = el[:, 0], az[:, 0], rng[:, 0], ok[:, 0]

    rows = []
    for i, s in enumerate(sats):
        if not ok[i] or el[i] < MIN_ELEVATION_DEG:
            continue
        off_boresight = angular_separation_deg(
            el[i], az[i], state.boresight_el_deg, state.boresight_az_deg % 360.0)
        rows.append((off_boresight, s, el[i], az[i], rng[i]))
    rows.sort()

    print(f"Satellites above {MIN_ELEVATION_DEG:.0f}° elevation at "
          f"{now.strftime('%H:%M:%S')} UTC ({catalog} catalogue, {age:.1f} h old), "
          f"sorted by angle off boresight "
          f"(az {state.boresight_az_deg:.1f}° el {state.boresight_el_deg:.1f}°):")
    for off, s, e, a, r in rows[:args.count]:
        print(f"  {s.name:<17} NORAD {s.norad:<6} el {e:4.1f}° az {a:5.1f}° "
              f"{r:4.0f} km · {off:4.1f}° off boresight")
    print(f"({len(rows)} total above the elevation cut)")


def cmd_locate(args):
    """Recover the dish location from observed beam tracks (useful when the
    dish GPS is policy-blocked) and save it to location.json for the other
    commands to use. Resets the obstruction map per slot."""
    import locate as locate_mod
    from matcher import TrailPoint

    sats, catalog, age = load_catalogue(args.catalog, args.tle_max_age,
                                        offline=args.offline)
    print(f"Catalogue: {len(sats)} satellites ({catalog}, {age:.1f} h old)")

    segments = []
    if args.from_log:
        # Note: reuses the el/az annotated at log time. If the projection
        # model changes, re-annotate from the raw x/y + logged dish state
        # instead (analyze_geometry.py shows how).
        for line in Path(args.from_log).read_text().splitlines():
            rec = json.loads(line)
            points = [TrailPoint(t=p["t"], x=p["x"], y=p["y"],
                                 n_new=p["n_new"], el=p["el"], az=p["az"])
                      for p in rec["points"]]
            segments.extend(s for s in segment_trail(points)
                            if len(s.points) >= 3)
    else:
        dish = Dish(target=args.target)
        frame = dish.get_map().frame
        print(f"Collecting {args.slots} slots of beam tracks "
              "(this resets the obstruction map each slot)...")
        for i in range(args.slots):
            boundary = wait_for_slot_boundary()
            state = dish.get_state()
            samples = collect_slot(dish, boundary)
            points = extract_trail(samples)
            annotate_azel(points, frame, samples[0].geom, state)
            good = [s for s in segment_trail(points) if len(s.points) >= 3]
            segments.extend(good)
            print(f"  slot {i + 1}/{args.slots}: {len(points)} points, "
                  f"{len(good)} usable segment(s)")
        dish.close()

    if len(segments) < 2:
        sys.exit(f"Only {len(segments)} usable segments; need at least 2 "
                 "(try more slots)")

    seed = parse_latlon(args.seed)[:2] if args.seed else None
    box = None
    if args.box:
        parts = [float(x) for x in args.box.split(",")]
        if len(parts) != 4:
            sys.exit("--box needs LAT0,LAT1,LON0,LON1")
        box = tuple(parts)
    if seed:
        where = f" around {seed}"
    elif box:
        where = f" in box {box}"
    else:
        b = locate_mod.DEFAULT_BOX
        where = f" in the default box ({b[0]:.0f}..{b[1]:.0f}N, {b[2]:.0f}..{b[3]:.0f}E)"
        print("NOTE: the default search area covers Europe only — pass "
              "--seed LAT,LON or --box LAT0,LAT1,LON0,LON1 elsewhere.")
    print(f"Grid-searching{where} over {len(segments)} segments...")
    lat, lon, residual, per_segment = locate_mod.locate(segments, sats,
                                                        seed=seed, box=box)

    print(f"\nBest location: {lat:.4f}, {lon:.4f}  "
          f"(mean residual {residual:.2f}°, ~{residual * 9.6:.0f} km position "
          "uncertainty at 550 km)")
    for eps, name in per_segment:
        print(f"  segment matched {name:<17} ε={eps:4.1f}°")
    if residual > 4.0:
        print(f"Fit is poor (residual {residual:.1f}°) — NOT saving to "
              f"{LOCATION_FILE.name}; try more slots, or --seed/--box if the "
              "search area was wrong")
    else:
        LOCATION_FILE.write_text(json.dumps(
            {"lat": round(lat, 4), "lon": round(lon, 4), "alt_m": 100.0}))
        print(f"Saved to {LOCATION_FILE} — identify/fov will use it "
              "automatically")


def cmd_tle(args):
    sats, catalog, age = load_catalogue(
        args.catalog, 0.0 if args.refresh else args.tle_max_age,
        offline=args.offline)
    dtc = sum(1 for s in sats if "[DTC]" in s.name)
    print(f"{catalog} catalogue: {len(sats)} satellites "
          f"({dtc} direct-to-cell, included in matching), {age:.1f} h old")


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--target", default=None,
                   help="dish gRPC host:port (default 192.168.100.1:9200)")
    p.add_argument("--location", default=None, metavar="LAT,LON[,ALT_M]",
                   help="observer position (WGS84 degrees)")
    p.add_argument("--save-location", action="store_true",
                   help="persist the resolved location for future runs")
    p.add_argument("--catalog", choices=["sup", "group"], default="sup",
                   help="CelesTrak source: supplemental (SpaceX ephemerides, "
                        "default) or the standard group set")
    p.add_argument("--tle-max-age", type=float, default=12.0, metavar="HOURS",
                   help="re-download the catalogue when older than this")
    p.add_argument("--offline", action="store_true",
                   help="never download; use cached TLEs only")
    p.add_argument("-v", "--verbose", action="store_true")

    sub = p.add_subparsers(dest="command", required=True)

    pi = sub.add_parser("identify", help="observe slots and identify the "
                                         "serving satellite (resets the obstruction map!)")
    pi.add_argument("--slots", type=int, default=None,
                    help="observe exactly N slots (default: adaptive)")
    pi.add_argument("--min-slots", type=int, default=2,
                    help="adaptive mode: observe at least this many slots")
    pi.add_argument("--max-slots", type=int, default=8,
                    help="adaptive mode: give up after this many slots")
    pi.add_argument("--watch", action="store_true",
                    help="keep observing until interrupted")
    pi.add_argument("--log", default=None, metavar="FILE.jsonl",
                    help="append per-slot results as JSON lines")
    pi.add_argument("--satellite-info", action="store_true",
                    help="as each new satellite is identified, show its "
                         "launch/age/orbit/status (SATCAT)")
    pi.add_argument("--dwells", action="store_true",
                    help="print only serving-satellite changes: one block per "
                         "dwell with start/end timestamps, transfer and the "
                         "all-time tally; combine with --satellite-info for "
                         "a full info block per new satellite (implies --watch)")
    pi.add_argument("--log-dwells", nargs="?", default=None,
                    const="dwells.jsonl", metavar="FILE.jsonl",
                    help="append one JSON record per closed dwell, to "
                         "dwells.jsonl unless a file is given "
                         "(implies --dwells)")
    pi.set_defaults(func=cmd_identify)

    pn = sub.add_parser("info", help="show launch/age/orbit/status for "
                                     "satellites by name or NORAD id")
    pn.add_argument("satellites", nargs="+", metavar="NAME_OR_NORAD")
    pn.set_defaults(func=cmd_info)

    pf = sub.add_parser("fov", help="list satellites currently near the dish FOV "
                                    "(read-only, no map reset)")
    pf.add_argument("--count", type=int, default=15, help="rows to print")
    pf.set_defaults(func=cmd_fov)

    pl = sub.add_parser("locate", help="recover the dish position from beam "
                                       "tracks and save it to location.json "
                                       "(resets the obstruction map)")
    pl.add_argument("--slots", type=int, default=6,
                    help="slots of tracks to collect live")
    pl.add_argument("--from-log", default=None, metavar="FILE.jsonl",
                    help="reuse tracks from an identify --log file instead "
                         "of collecting live")
    pl.add_argument("--seed", default=None, metavar="LAT,LON",
                    help="approximate position to search around")
    pl.add_argument("--box", default=None, metavar="LAT0,LAT1,LON0,LON1",
                    help="coarse search area (default: Europe, 45..72N -11..32E)")
    pl.set_defaults(func=cmd_locate)

    pt = sub.add_parser("tle", help="show/refresh the cached catalogue")
    pt.add_argument("--refresh", action="store_true", help="force re-download")
    pt.set_defaults(func=cmd_tle)

    args = p.parse_args()
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.WARNING,
                        format="%(levelname)s %(name)s: %(message)s")
    try:
        args.func(args)
    except RuntimeError as e:
        # friendly form of e.g. "no cached catalogue with --offline"
        if args.verbose:
            raise
        sys.exit(f"error: {e}")


if __name__ == "__main__":
    main()
