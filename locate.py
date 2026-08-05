"""Solve for the observer location from observed beam tracks.

The dish's GPS position is often blocked by policy ("allow access on local
network" off), but the observed sky tracks constrain it: only near the true
location does *some* catalogue satellite line up with each track. We grid
search lat/lon minimizing, per track segment, the best achievable mean
angular separation, summed over segments.

Satellite ECEF positions are observer-independent, so the catalogue is
propagated once per segment and each candidate location only pays for the
ENU/az-el transform (cheap numpy).
"""

import logging

import numpy as np

from matcher import MIN_ELEVATION_DEG
from propagation import altaz_from_ecef, angsep_np, batch_ecef

logger = logging.getLogger(__name__)


class SegmentData:
    """Precomputed per-segment arrays for the location search."""

    def __init__(self, points, satellites):
        self.obs_el = np.array([p.el for p in points])
        self.obs_az = np.array([p.az for p in points])
        times = [p.dt for p in points]
        ecef, ok = batch_ecef([s.satrec for s in satellites], times)
        keep = ok.all(axis=1)
        self.ecef = ecef[keep]          # (n_ok_sats, n_points, 3)
        self.names = [s.name for s, k in zip(satellites, keep) if k]

    def best_eps(self, lat, lon, alt_m=100.0):
        """(eps_deg, sat_name) of the best-matching satellite from here."""
        el, az, _ = altaz_from_ecef(self.ecef, lat, lon, alt_m)
        eps = angsep_np(el, az, self.obs_el, self.obs_az).mean(axis=1)
        mid = el[:, el.shape[1] // 2]
        eps = np.where(mid > MIN_ELEVATION_DEG, eps, np.inf)
        i = int(np.argmin(eps))
        return float(eps[i]), self.names[i]


def objective(segments_data, lat, lon):
    return float(np.mean([sd.best_eps(lat, lon)[0] for sd in segments_data]))


def grid_search(segments_data, lat_range, lon_range, step, progress=None):
    lats = np.arange(lat_range[0], lat_range[1] + step / 2, step)
    lons = np.arange(lon_range[0], lon_range[1] + step / 2, step)
    best = (np.inf, None, None)
    for i, lat in enumerate(lats):
        for lon in lons:
            val = objective(segments_data, lat, lon)
            if val < best[0]:
                best = (val, lat, lon)
        if progress:
            progress(i + 1, len(lats), best)
    return best


def locate(segments, satellites, seed=None, span_deg=4.0):
    """Multi-stage grid search. seed=(lat, lon) narrows the first stage;
    without it the whole 45..72N / -11..32E box is scanned (Starlink dishes
    in Europe; widen for other continents).

    Returns (lat, lon, residual_deg, per_segment: [(eps, name)]).
    """
    segments_data = [SegmentData(s.points, satellites) for s in segments]

    if seed:
        stages = [((seed[0] - span_deg, seed[0] + span_deg),
                   (seed[1] - span_deg * 2, seed[1] + span_deg * 2), 0.5)]
    else:
        stages = [((45.0, 72.0), (-11.0, 32.0), 1.0)]

    best = None
    for lat_range, lon_range, step in stages:
        def progress(done, total, cur):
            logger.info("coarse scan %d/%d rows, best so far %.2f deg at %.2f,%.2f",
                        done, total, cur[0], cur[1] or 0, cur[2] or 0)
        best = grid_search(segments_data, lat_range, lon_range, step, progress)

    # refine around the winner: 0.1 deg then 0.02 deg (~2 km)
    for span, step in ((1.0, 0.1), (0.15, 0.02)):
        _, lat, lon = best
        best = grid_search(segments_data,
                           (lat - span, lat + span),
                           (lon - span * 2, lon + span * 2), step)

    val, lat, lon = best
    per_segment = [sd.best_eps(lat, lon) for sd in segments_data]
    return lat, lon, val, per_segment
