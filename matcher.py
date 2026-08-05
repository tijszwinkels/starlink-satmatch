"""Trail extraction from obstruction-map samples and satellite matching.

Method (after Ahangarpour et al. LEO-NET'24 / arXiv:2601.13790):
- Within one 15 s scheduling slot (boundaries at :12/:27/:42/:57 UTC),
  reset the map and sample it ~1 Hz. Pixels newly lit between consecutive
  samples are where the beam actually pointed during that second.
- Convert lit pixels to (el, az), compare against SGP4-propagated positions
  of the whole catalogue at the matching timestamps, and score candidates by
  mean angular separation plus a trajectory-direction term.
- A slot can contain more than one satellite (intra-slot beam switching), so
  the trail is split into segments wherever consecutive points jump.
"""

import math
from dataclasses import dataclass, field
from datetime import datetime, timezone

import numpy as np

from geometry import (FRAME_UT, angular_separation_deg, bearing_deg,
                      pixel_to_azel, pixel_to_azel_bs, roll_from_quaternion)
from propagation import batch_altaz

# Observed residual scale with the boresight-frame projection model
# (mean best-eps 0.79 deg on 15 real segments; see notes).
SIGMA_DEG = 1.25
# Candidates with mean separation above this are not worth listing.
MAX_EPS_DEG = 10.0
# Consecutive trail points further apart than this indicate a beam switch.
# A serving satellite moves <~1.5 px/s on the 80/62 deg/px grid.
SEGMENT_JUMP_PX = 5.0
# Prefilter: satellites below this elevation can't be in the map FOV.
MIN_ELEVATION_DEG = 8.0


@dataclass
class TrailPoint:
    t: float          # unix time
    x: float          # sub-pixel column
    y: float          # sub-pixel row
    n_new: int        # how many pixels lit up in this interval
    el: float = 0.0
    az: float = 0.0

    @property
    def dt(self):
        return datetime.fromtimestamp(self.t, tz=timezone.utc)


@dataclass
class Candidate:
    name: str
    norad: int
    eps_deg: float          # mean angular separation over the segment
    bearing_diff_deg: float
    likelihood: float       # normalized within the segment's candidate list
    el_deg: float           # predicted position at segment midpoint
    az_deg: float
    range_km: float


@dataclass
class Segment:
    points: list
    candidates: list = field(default_factory=list)

    @property
    def t_start(self):
        return self.points[0].t

    @property
    def t_end(self):
        return self.points[-1].t


def _largest_cluster(coords):
    """Largest 8-connected cluster among (row, col) coords; tiny inputs are
    common so a simple union-by-BFS is fine."""
    coords = [tuple(c) for c in coords]
    remaining = set(coords)
    best = []
    while remaining:
        seed = remaining.pop()
        blob = [seed]
        frontier = [seed]
        while frontier:
            r, c = frontier.pop()
            for nb in [(r + dr, c + dc) for dr in (-1, 0, 1) for dc in (-1, 0, 1)]:
                if nb in remaining:
                    remaining.remove(nb)
                    blob.append(nb)
                    frontier.append(nb)
        if len(blob) > len(best):
            best = blob
    return best


def extract_trail(samples):
    """Turn a slot's map samples into trail points.

    The first sample is only the baseline (immune to a slow server-side
    reset); each later sample contributes the centroid of pixels that
    transitioned unlit -> lit since the previous sample.
    """
    points = []
    prev = samples[0]
    for s in samples[1:]:
        new = s.lit & ~prev.lit
        t_mid = (prev.t + s.t) / 2.0  # pixel lit somewhere in this interval
        prev = s
        coords = np.argwhere(new)
        if coords.size == 0:
            continue
        if len(coords) > 1:
            spread = coords.max(axis=0) - coords.min(axis=0)
            if max(spread) > SEGMENT_JUMP_PX:
                coords = np.array(_largest_cluster(coords))
        cy, cx = coords.mean(axis=0)
        points.append(TrailPoint(t=t_mid, x=float(cx), y=float(cy),
                                 n_new=len(coords)))
    return points


def annotate_azel(points, frame, geom, dish_state):
    """Convert trail pixels to (el, az) using the dish's orientation.

    FRAME_UT uses the boresight-centered projection with image roll derived
    from the attitude quaternion (image-down = dish body -y); FRAME_EARTH
    is the plain north-up zenith projection.
    """
    if frame == FRAME_UT:
        el_b = dish_state.boresight_el_deg
        az_b = dish_state.boresight_az_deg
        q = getattr(dish_state, "ned2dish_q", None)
        roll = roll_from_quaternion(q, el_b, az_b) if q else 0.0
        for p in points:
            p.el, p.az = pixel_to_azel_bs(p.x, p.y, geom, el_b, az_b, roll)
    else:
        for p in points:
            p.el, p.az = pixel_to_azel(p.x, p.y, frame, geom)
    return points


def segment_trail(points, jump_px=SEGMENT_JUMP_PX, max_gap_s=4.0):
    """Split a trail where the beam jumped (intra-slot satellite switch) or
    where sampling gapped."""
    segments = []
    current = []
    for p in points:
        if current:
            q = current[-1]
            if (math.hypot(p.x - q.x, p.y - q.y) > jump_px
                    or p.t - q.t > max_gap_s):
                segments.append(Segment(points=current))
                current = []
        current.append(p)
    if current:
        segments.append(Segment(points=current))
    return segments


def score_segment(segment, satellites, lat, lon, alt_m, min_points=3):
    """Fill segment.candidates (sorted best-first). Returns the segment.

    Two-stage: coarse prefilter of the whole catalogue at the segment
    midpoint (elevation cut), then per-point scoring of the survivors.
    """
    pts = segment.points
    if len(pts) < min_points:
        return segment

    times = [p.dt for p in pts]
    mid = [times[len(times) // 2]]
    satrecs = [s.satrec for s in satellites]

    el_mid, _, _, ok_mid = batch_altaz(satrecs, mid, lat, lon, alt_m)
    keep = np.where(ok_mid[:, 0] & (el_mid[:, 0] > MIN_ELEVATION_DEG))[0]
    if keep.size == 0:
        return segment

    cand_sats = [satellites[i] for i in keep]
    el, az, rng, ok = batch_altaz([s.satrec for s in cand_sats], times,
                                  lat, lon, alt_m)

    obs_el = np.array([p.el for p in pts])
    obs_az = np.array([p.az for p in pts])
    obs_bearing = bearing_deg(pts[0].el, pts[0].az, pts[-1].el, pts[-1].az)

    scored = []
    for i, sat in enumerate(cand_sats):
        if not ok[i].all():
            continue
        seps = [angular_separation_deg(obs_el[j], obs_az[j], el[i, j], az[i, j])
                for j in range(len(pts))]
        eps = float(np.mean(seps))
        if eps > MAX_EPS_DEG:
            continue
        sat_bearing = bearing_deg(el[i, 0], az[i, 0], el[i, -1], az[i, -1])
        dbear = abs(obs_bearing - sat_bearing) % 360.0
        if dbear > 180.0:
            dbear = 360.0 - dbear
        m = len(pts) // 2
        scored.append(Candidate(
            name=sat.name, norad=sat.norad, eps_deg=eps,
            bearing_diff_deg=dbear, likelihood=0.0,
            el_deg=float(el[i, m]), az_deg=float(az[i, m]),
            range_km=float(rng[i, m]),
        ))

    # Score = separation plus a damped direction term; likelihoods are
    # normalized from the same score so ranking and p always agree.
    def score(c):
        return c.eps_deg + 0.25 * c.bearing_diff_deg

    scored.sort(key=score)
    weights = [math.exp(-0.5 * (score(c) / SIGMA_DEG) ** 2) for c in scored]
    total = sum(weights) or 1.0
    for c, w in zip(scored, weights):
        c.likelihood = w / total
    segment.candidates = scored
    return segment
