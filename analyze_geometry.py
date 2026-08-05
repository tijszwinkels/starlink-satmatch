#!/usr/bin/env python3
"""One-off spike analysis: which FRAME_UT projection model fits the data?

Compares, on a logged identify run (JSONL with raw pixel trails + per-slot
dish state incl. quaternion):

  A      -- LEOViz tilt-shift approximation (current production model)
  B(rho) -- azimuthal-equidistant about the boresight axis, with image roll
            rho as a free parameter

and reports the roll the ned2dish quaternion would imply under each
image-axis hypothesis, to see whether the fitted roll matches one of them.

Usage: analyze_geometry.py LOG.jsonl [LOG2.jsonl ...]
"""

import json
import sys

import numpy as np

import locate as locate_mod
from geometry import MapGeometry, pixel_to_azel, pixel_to_azel_bs, FRAME_UT, _boresight_frame
from matcher import TrailPoint, segment_trail, MIN_ELEVATION_DEG
from propagation import altaz_from_ecef, angsep_np, batch_ecef
from tle import load_catalogue

GEOM = MapGeometry()
# Coarse search seed near the observer; refine with your own position or a
# `locate` result before re-running the analysis.
SEED = (62.0, 7.0)


def load_slots(paths):
    slots = []
    for path in paths:
        for line in open(path):
            rec = json.loads(line)
            if rec["points"]:
                slots.append(rec)
    return slots


def annotate(slots, model, rho=0.0):
    """Re-annotate raw pixels under a model; returns segments (>=3 pts)."""
    segments = []
    for rec in slots:
        d = rec["dish"]
        points = [TrailPoint(t=p["t"], x=p["x"], y=p["y"], n_new=p["n_new"])
                  for p in rec["points"]]
        for p in points:
            if model == "A":
                p.el, p.az = pixel_to_azel(p.x, p.y, FRAME_UT, GEOM,
                                           d["tilt_deg"], d["boresight_az_deg"])
            else:
                p.el, p.az = pixel_to_azel_bs(p.x, p.y, GEOM,
                                              d["boresight_el_deg"],
                                              d["boresight_az_deg"], rho)
        segments.extend(s for s in segment_trail(points) if len(s.points) >= 3)
    return segments


class FixedLocEvaluator:
    """Mean best-eps across segments at a fixed location. Satellite tracks
    are location- and model-independent, so they are precomputed once per
    segment; each (model, rho) evaluation is then pure trig on the pixels."""

    def __init__(self, slots, satellites, lat, lon):
        self.slots = slots
        base = annotate(slots, "A")  # segmentation is annotation-independent
        self.seg_keys = [tuple(p.t for p in s.points) for s in base]
        self.tracks = {}
        for s in base:
            key = tuple(p.t for p in s.points)
            ecef, ok = batch_ecef([x.satrec for x in satellites],
                                  [p.dt for p in s.points])
            el, az, _ = altaz_from_ecef(ecef[ok.all(axis=1)], lat, lon, 100.0)
            self.tracks[key] = (el, az)

    def mean_eps(self, model, rho=0.0):
        segs = annotate(self.slots, model, rho)
        vals = []
        for s in segs:
            key = tuple(p.t for p in s.points)
            if key not in self.tracks:
                continue
            el, az = self.tracks[key]
            obs_el = np.array([p.el for p in s.points])
            obs_az = np.array([p.az for p in s.points])
            eps = angsep_np(el, az, obs_el, obs_az).mean(axis=1)
            mid = el[:, el.shape[1] // 2]
            eps = np.where(mid > MIN_ELEVATION_DEG, eps, np.inf)
            vals.append(eps.min())
        return float(np.mean(vals))


def quaternion_rolls(slots):
    """Roll implied by the mean logged quaternion for each image-down
    hypothesis (dish body axis, ENU-projected onto the plane perp to b)."""
    qs = np.array([s["dish"]["ned2dish_q"] for s in slots
                   if "ned2dish_q" in s["dish"]])
    if qs.size == 0:
        return {}
    w, x, y, z = qs.mean(axis=0) / np.linalg.norm(qs.mean(axis=0))
    R = np.array([
        [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
        [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
        [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]])
    # columns of R = dish body axes in NED; NED -> ENU
    axes_ned = {"+x": R[:, 0], "-x": -R[:, 0], "+y": R[:, 1], "-y": -R[:, 1]}
    d0 = slots[0]["dish"]
    b, u1, u2 = _boresight_frame(d0["boresight_el_deg"], d0["boresight_az_deg"])
    out = {}
    for name, a_ned in axes_ned.items():
        a_enu = np.array([a_ned[1], a_ned[0], -a_ned[2]])
        out[name] = float(np.degrees(np.arctan2(a_enu @ np.array(u2),
                                                a_enu @ np.array(u1))))
    return out


def main():
    slots = load_slots(sys.argv[1:])
    print(f"{len(slots)} slots loaded")
    sats, _, _ = load_catalogue("sup", max_age_hours=1e9, offline=True)

    ev = FixedLocEvaluator(slots, sats, *SEED)
    print(f"\nAt fixed location {SEED}:")
    print(f"  model A (tilt-shift):      mean best-eps {ev.mean_eps('A'):.2f} deg")

    rolls = np.arange(-180.0, 180.0, 5.0)
    vals = [ev.mean_eps("B", r) for r in rolls]
    best_i = int(np.argmin(vals))
    print(f"  model B coarse roll scan:  best rho {rolls[best_i]:.0f} deg "
          f"-> {vals[best_i]:.2f} deg")
    fine = np.arange(rolls[best_i] - 5.0, rolls[best_i] + 5.01, 0.5)
    fvals = [ev.mean_eps("B", r) for r in fine]
    rho0 = float(fine[int(np.argmin(fvals))])
    print(f"  model B fine roll scan:    best rho {rho0:.1f} deg "
          f"-> {min(fvals):.2f} deg")

    print("\nQuaternion-implied roll per image-down hypothesis:")
    for name, r in quaternion_rolls(slots).items():
        print(f"  image-down = dish {name}: rho {r:7.1f} deg")

    # Re-fit the location under model B(rho0), then re-check rho there.
    segs = annotate(slots, "B", rho0)
    lat, lon, res, per_seg = locate_mod.locate(segs, sats, seed=SEED, span_deg=2.0)
    print(f"\nmodel B(rho={rho0:.1f}) location fit: {lat:.4f}, {lon:.4f} "
          f"residual {res:.2f} deg")
    ev2 = FixedLocEvaluator(slots, sats, lat, lon)
    fvals2 = [ev2.mean_eps("B", r) for r in fine]
    rho1 = float(fine[int(np.argmin(fvals2))])
    print(f"  re-scanned roll at that location: rho {rho1:.1f} deg "
          f"-> {min(fvals2):.2f} deg")
    print(f"  model A at that location:         {ev2.mean_eps('A'):.2f} deg")

    print("\nPer-segment matches under model B:")
    for eps, name in per_seg:
        print(f"  {name:<17} eps {eps:4.1f} deg")


if __name__ == "__main__":
    main()
