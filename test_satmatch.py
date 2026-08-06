#!/usr/bin/env python3
"""Tests for satmatch. Run: venv/bin/python test_satmatch.py

Uses plain asserts + a tiny runner (no pytest dependency). The propagation
oracle test and the end-to-end test need the cached TLE file and skyfield.
"""

import math
import sys
from datetime import timedelta, timezone, datetime

import numpy as np

from geometry import (FRAME_EARTH, FRAME_UT, MapGeometry, angular_separation_deg,
                      azel_to_pixel, azel_to_pixel_bs, bearing_deg, pixel_to_azel,
                      roll_from_quaternion)
from matcher import Segment, extract_trail, annotate_azel, segment_trail, score_segment
from propagation import batch_altaz, gstime_rad
import tle

GEOM = MapGeometry()
TILT = 26.0
BS_AZ = -59.8  # matches the real dish: WNW-facing, ~26 deg tilt
TEST_LAT, TEST_LON, TEST_ALT = 59.95, 10.75, 100.0


class FakeSample:
    def __init__(self, t, lit):
        self.t = t
        self.lit = lit


class FakeState:
    # zero-roll dish state matching the synthetic renders
    boresight_el_deg = 90.0 - TILT
    boresight_az_deg = BS_AZ
    tilt_deg = TILT
    ned2dish_q = None


STATE = FakeState()


def test_roundtrip_frames():
    for frame in (FRAME_EARTH, FRAME_UT):
        for el in (15.0, 40.0, 64.0, 85.0):
            for az in (0.0, 47.0, 180.0, 300.2):
                x, y = azel_to_pixel(el, az, frame, GEOM, TILT, BS_AZ)
                el2, az2 = pixel_to_azel(x, y, frame, GEOM, TILT, BS_AZ)
                sep = angular_separation_deg(el, az, el2, az2)
                assert sep < 1e-6, (frame, el, az, el2, az2)  # acos noise near 0


def test_frame_ut_center_is_boresight():
    el, az = pixel_to_azel(GEOM.center, GEOM.center, FRAME_UT, GEOM, TILT, BS_AZ)
    assert abs(el - (90.0 - TILT)) < 1e-9, el          # boresight elevation
    assert abs((az - BS_AZ % 360.0)) % 360.0 < 1e-9, az  # boresight azimuth


def test_frame_earth_up_is_north():
    el, az = pixel_to_azel(GEOM.center, 10.0, FRAME_EARTH, GEOM)
    assert az < 1e-9, az
    assert el < 90.0
    # right of center = East
    _, az = pixel_to_azel(GEOM.center + 20, GEOM.n_rows - GEOM.center, FRAME_EARTH, GEOM)
    assert abs(az - 90.0) < 1.0, az


def test_gstime_matches_sgp4():
    from sgp4.propagation import gstime
    for jd in (2451545.0, 2460000.5, 2461257.123):
        assert abs(gstime_rad(jd) - gstime(jd)) < 1e-9


def _load_test_sats(n=None):
    path = tle.CACHE_DIR / "sup-starlink.tle"
    if not path.exists():
        print("  SKIP (no cached TLE file)")
        return None
    sats = tle.parse_tle_file(path)
    return sats[:n] if n else sats


def test_dtc_included_by_default():
    sats = _load_test_sats()
    if sats is None:
        return
    assert any("[DTC]" in s.name for s in sats), "DTC missing from default load"
    excl = tle.parse_tle_file(tle.CACHE_DIR / "sup-starlink.tle",
                              include_dtc=False)
    assert not any("[DTC]" in s.name for s in excl)
    assert len(excl) < len(sats)


def test_batch_altaz_vs_skyfield():
    try:
        from skyfield.api import EarthSatellite, load, wgs84
    except ImportError:
        print("  SKIP (skyfield not installed)")
        return
    path = tle.CACHE_DIR / "sup-starlink.tle"
    if not path.exists():
        print("  SKIP (no cached TLE file)")
        return
    lines = path.read_text().splitlines()
    ts = load.timescale()
    topos = wgs84.latlon(TEST_LAT, TEST_LON, TEST_ALT)

    checked = 0
    for i in range(0, 30, 3):
        name, l1, l2 = lines[i].strip(), lines[i + 1], lines[i + 2]
        sf_sat = EarthSatellite(l1, l2, name, ts)
        # evaluate near this TLE's own epoch so SGP4 error is tiny
        t0 = sf_sat.epoch.utc_datetime() + timedelta(minutes=30)
        times = [t0, t0 + timedelta(seconds=200)]

        el, az, rng, ok = batch_altaz([sf_sat.model], times,
                                      TEST_LAT, TEST_LON, TEST_ALT)
        assert ok.all(), name
        for j, t in enumerate(times):
            alt_o, az_o, dist_o = (sf_sat - topos).at(ts.from_datetime(t)).altaz()
            sep = angular_separation_deg(el[0, j], az[0, j],
                                         alt_o.degrees, az_o.degrees)
            assert sep < 0.25, (name, sep)
            assert abs(rng[0, j] - dist_o.km) < 5.0, (name, rng[0, j], dist_o.km)
        checked += 1
    assert checked == 10


def _pick_visible_sat(sats, t, exclude=(), off_max=40.0):
    """A satellite well inside the FOV cone and steadily visible."""
    el, az, _, ok = batch_altaz([s.satrec for s in sats], [t], TEST_LAT,
                                TEST_LON, TEST_ALT)
    best = None
    for i, s in enumerate(sats):
        if s.norad in exclude or not ok[i, 0] or not (30.0 < el[i, 0] < 78.0):
            continue
        off = angular_separation_deg(el[i, 0], az[i, 0], 90.0 - TILT, BS_AZ % 360)
        if off < off_max and (best is None or off < best[1]):
            best = (i, off)
    return sats[best[0]] if best else None


def _render_track(target, t0, n=15):
    """Render a satellite's true sky track into accumulating fake maps."""
    times = [t0 + timedelta(seconds=k) for k in range(n)]
    el, az, _, ok = batch_altaz([target.satrec], times, TEST_LAT, TEST_LON, TEST_ALT)
    assert ok.all()
    lit = np.zeros((GEOM.n_rows, GEOM.n_cols), dtype=bool)
    samples = []
    for k, t in enumerate(times):
        x, y = azel_to_pixel_bs(el[0, k], az[0, k], GEOM,
                                90.0 - TILT, BS_AZ, 0.0)
        lit = lit.copy()
        lit[round(y), round(x)] = True
        samples.append(FakeSample(t.timestamp(), lit))
    return samples


def test_end_to_end_synthetic():
    sats = _load_test_sats()
    if sats is None:
        return
    t0 = datetime.now(timezone.utc)
    target = None
    for _ in range(240):  # scan forward until a satellite is in the cone
        target = _pick_visible_sat(sats, t0)
        if target is not None:
            break
        t0 += timedelta(minutes=2)
    assert target is not None, "no satellite entered the test cone in 8 h"
    samples = _render_track(target, t0)

    points = extract_trail(samples)
    assert len(points) >= 5, len(points)  # slow tracks revisit pixels
    annotate_azel(points, FRAME_UT, GEOM, STATE)
    segments = segment_trail(points)
    assert len(segments) == 1, [len(s.points) for s in segments]
    seg = score_segment(segments[0], sats, TEST_LAT, TEST_LON, TEST_ALT)

    assert seg.candidates, "no candidates found"
    top = seg.candidates[0]
    assert top.norad == target.norad, (
        f"expected {target.name}, got {top.name} "
        f"(eps {top.eps_deg:.2f}, target eps "
        f"{[c.eps_deg for c in seg.candidates if c.norad == target.norad]})")
    assert top.eps_deg < 2.0, top.eps_deg  # pixel quantization noise only


def test_locate_recovers_position():
    from locate import locate as run_locate
    sats = _load_test_sats()
    if sats is None:
        return
    t0 = datetime.now(timezone.utc)

    segments = []
    seen = set()
    slot = 0
    # 4+ distinct-satellite tracks: 3 can under-constrain the position fit
    while len(segments) < 4 and slot < 60:
        t = t0 + timedelta(seconds=45 * slot)
        slot += 1
        target = _pick_visible_sat(sats, t, exclude=seen)
        if target is None:
            continue
        seen.add(target.norad)
        points = extract_trail(_render_track(target, t))
        annotate_azel(points, FRAME_UT, GEOM, STATE)
        segments.extend(s for s in segment_trail(points) if len(s.points) >= 3)
    assert len(segments) >= 4, len(segments)

    lat, lon, residual, _ = run_locate(
        segments, sats, seed=(TEST_LAT + 1.3, TEST_LON - 1.1))
    assert abs(lat - TEST_LAT) < 0.3, (lat, TEST_LAT)
    assert abs(lon - TEST_LON) < 0.6, (lon, TEST_LON)
    assert residual < 2.0, residual


def test_satinfo_version_heuristic():
    from satinfo import starlink_version
    assert starlink_version("2019-05-24").startswith("v0.9")
    assert starlink_version("2020-01-07").startswith("v1.0")
    assert starlink_version("2021-06-01").startswith("v1.5")
    assert starlink_version("2023-02-27").startswith("V2 Mini")
    assert starlink_version("2025-12-01").startswith("V2 Mini")
    # Gen1 70/97.6 deg shells kept getting v1.5 after the V2 Mini debut
    assert starlink_version("2023-03-03", incl_deg=70.0).startswith("v1.5")
    assert starlink_version("2023-03-03", incl_deg=53.2).startswith("V2 Mini")
    assert starlink_version("2026-01-22", incl_deg=97.3).startswith("V2 Mini")
    assert "direct-to-cell" in starlink_version("2024-01-02", "STARLINK-11072 [DTC]")
    assert starlink_version("") == "unknown"


def test_satinfo_live_entry():
    import satinfo
    if not satinfo.SATCAT_PATH.exists():
        print("  SKIP (no cached satcat.csv)")
        return
    sats = _load_test_sats()
    if sats is None:
        return
    satcat = satinfo.load_satcat(offline=True)
    sat = next(s for s in sats if s.name == "STARLINK-5539")
    entry = satcat[sat.norad]
    assert entry.launch_date == "2023-03-03", entry
    assert entry.ops_status_code == "+", entry
    peri, apo, incl, period = satinfo.orbit_from_satrec(sat.satrec)
    assert 60.0 < incl < 80.0, incl        # 70 deg shell
    assert 450.0 < peri <= apo < 700.0, (peri, apo)
    assert 90.0 < period < 100.0, period
    text = satinfo.format_info(sat, entry, True)
    assert "v1.5" in text and "Operational" in text and "70.0°" in text, text


def test_dwell_log_sequencing():
    import contextlib
    import io
    from matcher import Candidate
    from satmatch import DwellLog

    def seg(norad, name, t0, eps=1.0, p=1.0):
        s = Segment(points=[type("P", (), {"t": t0})(),
                            type("P", (), {"t": t0 + 13.0})()])
        s.candidates = [Candidate(name=name, norad=norad, eps_deg=eps,
                                  bearing_diff_deg=0.0, likelihood=p,
                                  el_deg=50.0, az_deg=100.0, range_km=600.0)]
        return s

    log = DwellLog(satcat={}, by_norad={})
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        log.observe([seg(111, "STARLINK-A", 1000.0)])   # opens A
        log.observe([seg(111, "STARLINK-A", 1015.0)])   # continues A
        log.observe([Segment(points=[])])               # no ID: no change
        weak = seg(222, "STARLINK-B", 1045.0, eps=8.0, p=1.0)
        log.observe([weak])                             # weak: must not switch
        log.observe([seg(222, "STARLINK-B", 1060.0)])   # confident B: switch
        log.close()
    text = out.getvalue()
    assert text.count("▶") == 2, text
    assert text.count("■") == 2, text
    assert text.index("STARLINK-A") < text.index("STARLINK-B")
    a_close = text.splitlines()[1]
    # window [987 (slot start), 1047 (boundary nearest the 1028/1060 mid)]
    assert "tracked 60 s" in a_close, a_close
    assert "confirmed in 2/4 slot(s)" in a_close, a_close
    assert "\n\n" in text                              # blank line between dwells


def test_dwell_log_jsonl_records():
    import contextlib
    import io
    import json as jsonlib
    from matcher import Candidate
    from satmatch import DwellLog

    def seg(norad, name, ts):
        s = Segment(points=[type("P", (), {"t": ts})(),
                            type("P", (), {"t": ts + 13.0})()])
        s.candidates = [Candidate(name=name, norad=norad, eps_deg=1.0,
                                  bearing_diff_deg=0.0, likelihood=1.0,
                                  el_deg=50.0, az_deg=100.0, range_km=600.0)]
        return s

    fh = io.StringIO()
    log = DwellLog(satcat=None, by_norad={}, log_fh=fh)
    with contextlib.redirect_stdout(io.StringIO()):
        log.observe([seg(111, "STARLINK-A", 1000.0)])
        log.observe([seg(222, "STARLINK-B", 1030.0)])
        log.close()
    recs = [jsonlib.loads(l) for l in fh.getvalue().splitlines()]
    assert len(recs) == 2, recs
    a, b = recs
    assert a["norad"] == 111 and b["norad"] == 222
    assert a["seconds"] == 30.0, a          # [987, 1017] slot-aligned
    assert a["end"] == b["start"], (a, b)   # contiguous windows
    assert a["slots_confirmed"] == 1 and a["mean_eps_deg"] == 1.0, a
    assert a["down_bytes"] is None, a       # no dish attached
    assert a["first_evidence"].startswith("1970-01-01T00:16:40"), a


def test_dwell_log_intra_slot_switch_keeps_midpoint():
    import contextlib
    import io
    from matcher import Candidate
    from satmatch import DwellLog

    def seg(norad, name, t0, t1):
        s = Segment(points=[type("P", (), {"t": t0})(),
                            type("P", (), {"t": t1})()])
        s.candidates = [Candidate(name=name, norad=norad, eps_deg=1.0,
                                  bearing_diff_deg=0.0, likelihood=1.0,
                                  el_deg=50.0, az_deg=100.0, range_km=600.0)]
        return s

    log = DwellLog(satcat=None, by_norad={})
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        # both satellites seen inside the same slot [987, 1002)
        log.observe([seg(111, "STARLINK-A", 988.0, 992.0),
                     seg(222, "STARLINK-B", 996.0, 999.0)])
    a_close = out.getvalue().splitlines()[1]
    # cut stays at the 994.0 midpoint: window [987, 994] -> 7 s
    assert "tracked 7 s" in a_close, a_close
    # the same-pass close must still count this slot as elapsed (not 1/0)
    assert "confirmed in 1/1 slot(s)" in a_close, a_close


def test_dwell_log_transfer_integration():
    import contextlib
    import io
    from matcher import Candidate
    from satmatch import DwellLog

    t0 = 2000.0

    class StubDish:
        def get_history_throughput(self, seconds):
            n = 60
            ts = [t0 - 10 + i for i in range(n)]
            return ts, [8e6] * n, [8e5] * n   # 1 MB/s down, 100 kB/s up

    def seg(norad, name, ts):
        s = Segment(points=[type("P", (), {"t": ts})(),
                            type("P", (), {"t": ts + 13.0})()])
        s.candidates = [Candidate(name=name, norad=norad, eps_deg=1.0,
                                  bearing_diff_deg=0.0, likelihood=1.0,
                                  el_deg=50.0, az_deg=100.0, range_km=600.0)]
        return s

    log = DwellLog(satcat={}, by_norad={}, dish=StubDish())
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        log.observe([seg(111, "STARLINK-A", t0)])
        log.observe([seg(222, "STARLINK-B", t0 + 30.0)])
    a_close = out.getvalue().splitlines()[1]
    # window [1992 (slot start before 2000), 2022 (snapped cut)]:
    # 31 one-second samples at 1 MB/s / 100 kB/s
    assert "↓ 31.0 MB" in a_close, a_close
    assert "↑ 3.1 MB" in a_close, a_close
    assert a_close.index("UTC") < a_close.index("↓"), a_close  # timestamp first
    # average rates over the 30 s window
    assert "(8.3 Mbit/s)" in a_close, a_close
    assert "(826.7 kbit/s)" in a_close, a_close


def test_sat_history_persistence(tmpdir="/tmp/satmatch-test-history.json"):
    import os
    from history import SatHistory
    if os.path.exists(tmpdir):
        os.remove(tmpdir)
    h = SatHistory(path=tmpdir)
    h.record_dwell(111, "STARLINK-A", slots=2, down_bytes=10e6, up_bytes=1e6,
                   seconds=30.0)
    h.record_dwell(111, "STARLINK-A", slots=3, down_bytes=5e6, up_bytes=0.5e6,
                   seconds=14.5)
    h.record_dwell(222, "STARLINK-B", slots=1, down_bytes=1e6, up_bytes=0.1e6,
                   seconds=15.0)
    # a fresh instance must see the accumulated state from disk
    h2 = SatHistory(path=tmpdir)
    e = h2.get(111)
    assert e["dwells"] == 2 and e["slots"] == 5, e
    assert e["down_bytes"] == 15e6 and e["up_bytes"] == 1.5e6, e
    assert e["seconds"] == 44.5, e
    assert e["name"] == "STARLINK-A" and "last_seen" in e, e
    assert h2.get(222)["dwells"] == 1
    assert h2.get(333) is None
    os.remove(tmpdir)


def test_dwell_log_history_line(tmppath="/tmp/satmatch-test-history2.json"):
    import contextlib
    import io
    import os
    from history import SatHistory
    from matcher import Candidate
    from satmatch import DwellLog

    if os.path.exists(tmppath):
        os.remove(tmppath)

    def seg(norad, name, ts):
        s = Segment(points=[type("P", (), {"t": ts})(),
                            type("P", (), {"t": ts + 13.0})()])
        s.candidates = [Candidate(name=name, norad=norad, eps_deg=1.0,
                                  bearing_diff_deg=0.0, likelihood=1.0,
                                  el_deg=50.0, az_deg=100.0, range_km=600.0)]
        return s

    log = DwellLog(satcat={}, by_norad={}, dish=None,
                   history=SatHistory(path=tmppath))
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        log.observe([seg(111, "STARLINK-A", 1000.0)])   # dwell 1: A
        log.observe([seg(222, "STARLINK-B", 1030.0)])   # closes A, opens B
        log.observe([seg(111, "STARLINK-A", 1060.0)])   # closes B, A again
        log.close()                                     # dwell 3 closes
    sums = [l for l in out.getvalue().splitlines() if "∑" in l]
    assert len(sums) == 3, sums
    assert "1 dwell ·" in sums[0] and "STARLINK" not in sums[0], sums[0]
    assert "1 dwell ·" in sums[1], sums[1]
    assert "2 dwells ·" in sums[2], sums[2]   # A's second dwell
    # A's slot-aligned windows: [987, 1017] + [1047, 1077] -> 60 s total
    assert "· 1 m 00 s ·" in sums[2], sums[2]
    os.remove(tmppath)


def test_segmentation_splits_jumps():
    pts_a = [(10.0 + k, 60.0 + k) for k in range(5)]
    pts_b = [(80.0 + k, 30.0 + k) for k in range(5)]
    samples = []
    lit = np.zeros((GEOM.n_rows, GEOM.n_cols), dtype=bool)
    t = 1000.0
    for x, y in pts_a + pts_b:
        lit = lit.copy()
        lit[int(y), int(x)] = True
        samples.append(FakeSample(t, lit))
        t += 1.0
    points = extract_trail(samples)
    segments = segment_trail(points)
    assert len(segments) == 2, [len(s.points) for s in segments]


def test_boresight_model_roundtrip_and_anchors():
    from geometry import azel_to_pixel_bs, pixel_to_azel_bs
    el_b, az_b = 90.0 - TILT, BS_AZ % 360.0
    for roll in (0.0, -12.5, 30.0):
        # round trip
        for el in (15.0, 40.0, 70.0):
            for az in (10.0, 150.0, 300.0):
                x, y = azel_to_pixel_bs(el, az, GEOM, el_b, az_b, roll)
                el2, az2 = pixel_to_azel_bs(x, y, GEOM, el_b, az_b, roll)
                assert angular_separation_deg(el, az, el2, az2) < 1e-5
        # center pixel is the boresight regardless of roll
        el2, az2 = pixel_to_azel_bs(GEOM.center, GEOM.center, GEOM, el_b, az_b, roll)
        assert angular_separation_deg(el2, az2, el_b, az_b) < 1e-6
    # zero roll: zenith sits tilt/deg_per_px straight up-image from center
    x, y = azel_to_pixel_bs(90.0, 0.0, GEOM, el_b, az_b, 0.0)
    assert abs(x - GEOM.center) < 1e-6
    assert abs(y - (GEOM.center - TILT / GEOM.deg_per_px)) < 1e-6
    # near the axis the tilt-shift model and this model agree
    for el, az in ((62.0, 295.0), (58.0, 310.0), (68.0, 285.0)):
        xa, ya = azel_to_pixel(el, az, FRAME_UT, GEOM, TILT, BS_AZ)
        xb, yb = azel_to_pixel_bs(el, az, GEOM, el_b, az_b, 0.0)
        assert math.hypot(xa - xb, ya - yb) < 1.0, (el, az, xa, ya, xb, yb)


def test_roll_from_quaternion_live_values():
    # regression lock against the live dish reading of 2026-08-04 and the
    # empirically fitted roll of ~23-24 deg (analyze_geometry.py)
    q = (0.0135, -0.4294, 0.8736, -0.2287)
    rho = roll_from_quaternion(q, 63.51, -60.45)
    assert abs(rho - 23.0) < 1.5, rho


def test_bearing_sanity():
    # due-east motion along the horizon-ish: bearing ~90
    b = bearing_deg(45.0, 100.0, 45.0, 110.0)
    assert 80.0 < b < 100.0, b


def test_dwell_log_publishes_current_satellite():
    import contextlib
    import io
    import json as jsonlib
    import tempfile
    from pathlib import Path
    from matcher import Candidate
    from satmatch import DwellLog

    def seg(norad, name, ts):
        s = Segment(points=[type("P", (), {"t": ts})(),
                            type("P", (), {"t": ts + 13.0})()])
        s.candidates = [Candidate(name=name, norad=norad, eps_deg=1.0,
                                  bearing_diff_deg=0.0, likelihood=1.0,
                                  el_deg=50.0, az_deg=100.0, range_km=600.0)]
        return s

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "current.json"
        log = DwellLog(satcat=None, by_norad={}, current_path=path)
        with contextlib.redirect_stdout(io.StringIO()):
            log.observe([seg(111, "STARLINK-A", 1000.0)])
            doc = jsonlib.loads(path.read_text())
            assert doc["norad"] == 111 and doc["name"] == "STARLINK-A", doc
            assert doc["since"].startswith("1970-01-01T00:16:40"), doc
            first_updated = doc["updated"]
            log.observe([seg(222, "STARLINK-B", 1030.0)])   # handover
            log.close()
        doc = jsonlib.loads(path.read_text())
        assert doc["norad"] == 222, doc
        assert doc["updated"] >= first_updated
        assert not path.with_suffix(".tmp").exists()   # atomic write cleaned up


def test_dwell_same_slot_segments_count_one_slot():
    # two confident segments for the same satellite within one slot (trail
    # split by a sampling gap) must not report "confirmed in 2/1 slot(s)"
    import contextlib
    import io
    from matcher import Candidate
    from satmatch import DwellLog

    def seg(norad, t0, t1, eps):
        s = Segment(points=[type("P", (), {"t": t0})(),
                            type("P", (), {"t": t1})()])
        s.candidates = [Candidate(name="STARLINK-A", norad=norad, eps_deg=eps,
                                  bearing_diff_deg=0.0, likelihood=1.0,
                                  el_deg=50.0, az_deg=100.0, range_km=600.0)]
        return s

    log = DwellLog(satcat=None, by_norad={})
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        # both segments inside the slot [987, 1002)
        log.observe([seg(111, 988.0, 992.0, eps=1.0),
                     seg(111, 997.0, 1000.0, eps=2.0)])
        log.close()
    close_line = out.getvalue().splitlines()[1]
    assert "confirmed in 1/1 slot(s)" in close_line, close_line
    assert "mean ε 1.5°" in close_line, close_line  # per-segment mean


def test_extract_trail_largest_cluster():
    # two clusters lighting up in one interval: centroid of the larger wins
    lit0 = np.zeros((GEOM.n_rows, GEOM.n_cols), dtype=bool)
    lit1 = lit0.copy()
    for c in (10, 11, 12):
        lit1[10, c] = True          # 3-px cluster around (11, 10)
    lit1[60, 60] = True             # far 1-px cluster
    points = extract_trail([FakeSample(1000.0, lit0), FakeSample(1001.0, lit1)])
    assert len(points) == 1, points
    assert abs(points[0].x - 11.0) < 1e-9 and abs(points[0].y - 10.0) < 1e-9, \
        (points[0].x, points[0].y)


def test_load_catalogue_fallback(tmpdir="/tmp/satmatch-test-cache"):
    # sup missing + offline -> falls back to the cached group file
    import shutil
    from pathlib import Path
    src = tle.CACHE_DIR / "sup-starlink.tle"
    if not src.exists():
        print("  SKIP (no cached TLE file)")
        return
    tmp = Path(tmpdir)
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True)
    lines = src.read_text().splitlines()[:6]  # two satellites are plenty
    (tmp / "group-starlink.tle").write_text("\n".join(lines) + "\n")
    orig = tle.CACHE_DIR
    tle.CACHE_DIR = tmp
    try:
        sats, cat, age = tle.load_catalogue("sup", offline=True)
        assert cat == "group", cat
        assert len(sats) == 2, len(sats)
        # and nothing at all -> a friendly RuntimeError, not a traceback
        (tmp / "group-starlink.tle").unlink()
        try:
            tle.load_catalogue("sup", offline=True)
            assert False, "expected RuntimeError"
        except RuntimeError as e:
            assert "without --offline" in str(e), e
    finally:
        tle.CACHE_DIR = orig
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            print(f"{name} ...")
            try:
                fn()
                print("  ok")
            except AssertionError as e:
                failures += 1
                print(f"  FAIL: {e}")
            except Exception as e:
                failures += 1
                print(f"  ERROR: {type(e).__name__}: {e}")
    print(f"\n{'FAILED' if failures else 'PASSED'} ({failures} failures)")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()

