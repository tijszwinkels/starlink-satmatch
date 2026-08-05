"""Fast vectorized satellite topocentric positions.

Uses sgp4.api.SatrecArray to propagate the whole ~10k-satellite catalogue in
one C call, then converts TEME -> ECEF (GMST rotation, polar motion ignored,
~0.01 deg effect) -> ENU -> az/el with numpy. Accuracy vs a full-featured
library (skyfield) is ~0.1 deg, far below the ~2.4 deg matching noise floor;
the unit tests check agreement against skyfield.
"""

import math
from datetime import datetime, timezone

import numpy as np
from sgp4.api import SatrecArray, jday

WGS84_A_KM = 6378.137
WGS84_F = 1.0 / 298.257223563
WGS84_E2 = WGS84_F * (2.0 - WGS84_F)


def gstime_rad(jd_ut1):
    """Greenwich mean sidereal time (radians), IAU-82 / Vallado gstime.
    Vectorized over jd_ut1 (array of full Julian dates)."""
    jd = np.asarray(jd_ut1, dtype=float)
    tut1 = (jd - 2451545.0) / 36525.0
    temp = (-6.2e-6 * tut1**3 + 0.093104 * tut1**2 +
            (876600.0 * 3600.0 + 8640184.812866) * tut1 + 67310.54841)
    # seconds of sidereal time -> radians (240 sidereal seconds per degree)
    return np.radians(temp / 240.0) % (2.0 * np.pi)


def observer_ecef_km(lat_deg, lon_deg, alt_m):
    """WGS84 geodetic -> ECEF position in km."""
    lat = math.radians(lat_deg)
    lon = math.radians(lon_deg)
    h = alt_m / 1000.0
    n = WGS84_A_KM / math.sqrt(1.0 - WGS84_E2 * math.sin(lat)**2)
    x = (n + h) * math.cos(lat) * math.cos(lon)
    y = (n + h) * math.cos(lat) * math.sin(lon)
    z = (n * (1.0 - WGS84_E2) + h) * math.sin(lat)
    return np.array([x, y, z])


def datetimes_to_jd(times):
    """List of aware-UTC datetimes -> (jd, fr) arrays for sgp4."""
    jds, frs = [], []
    for t in times:
        t = t.astimezone(timezone.utc)
        jd, fr = jday(t.year, t.month, t.day, t.hour, t.minute,
                      t.second + t.microsecond / 1e6)
        jds.append(jd)
        frs.append(fr)
    return np.array(jds), np.array(frs)


def batch_ecef(satrecs, times):
    """Propagate satellites to each time in Earth-fixed coordinates.

    Returns (ecef_km, ok): shapes (n_sats, n_times, 3) and (n_sats, n_times).
    Satellite ECEF positions are observer-independent, so callers evaluating
    many candidate observer locations only pay for this once.
    """
    jds, frs = datetimes_to_jd(times)
    err, r_teme, _ = SatrecArray(list(satrecs)).sgp4(jds, frs)
    theta = gstime_rad(jds + frs)  # (n_times,)
    cos_t, sin_t = np.cos(theta), np.sin(theta)
    x, y, z = r_teme[..., 0], r_teme[..., 1], r_teme[..., 2]
    ecef = np.stack([cos_t * x + sin_t * y,
                     -sin_t * x + cos_t * y,
                     z], axis=-1)
    return ecef, err == 0


def altaz_from_ecef(ecef, lat_deg, lon_deg, alt_m):
    """ECEF satellite positions -> topocentric (el, az, range) for one
    observer. Works on any (..., 3) array."""
    d = ecef - observer_ecef_km(lat_deg, lon_deg, alt_m)
    lat = math.radians(lat_deg)
    lon = math.radians(lon_deg)
    east = np.array([-math.sin(lon), math.cos(lon), 0.0])
    north = np.array([-math.sin(lat) * math.cos(lon),
                      -math.sin(lat) * math.sin(lon),
                      math.cos(lat)])
    up = np.array([math.cos(lat) * math.cos(lon),
                   math.cos(lat) * math.sin(lon),
                   math.sin(lat)])
    e = d @ east
    n = d @ north
    u = d @ up
    rng = np.linalg.norm(d, axis=-1)
    with np.errstate(invalid="ignore", divide="ignore"):
        el = np.degrees(np.arcsin(np.clip(u / rng, -1.0, 1.0)))
        az = (np.degrees(np.arctan2(e, n)) + 360.0) % 360.0
    return el, az, rng


def batch_altaz(satrecs, times, lat_deg, lon_deg, alt_m):
    """Propagate satellites and return (el, az, rng, ok), each
    (n_sats, n_times), for one observer position."""
    ecef, ok = batch_ecef(satrecs, times)
    el, az, rng = altaz_from_ecef(ecef, lat_deg, lon_deg, alt_m)
    return el, az, rng, ok


def angsep_np(el1, az1, el2, az2):
    """Vectorized great-circle separation in degrees; inputs in degrees,
    broadcast against each other."""
    e1, a1 = np.radians(el1), np.radians(az1)
    e2, a2 = np.radians(el2), np.radians(az2)
    cos_sep = (np.sin(e1) * np.sin(e2) +
               np.cos(e1) * np.cos(e2) * np.cos(a1 - a2))
    return np.degrees(np.arccos(np.clip(cos_sep, -1.0, 1.0)))


def parse_latlon(s):
    """Parse 'lat,lon' or 'lat,lon,alt_m' into a (lat, lon, alt_m) tuple."""
    parts = [float(p) for p in s.split(",")]
    if len(parts) == 2:
        parts.append(0.0)
    if len(parts) != 3 or not (-90 <= parts[0] <= 90) or not (-180 <= parts[1] <= 360):
        raise ValueError(f"cannot parse location {s!r}; expected lat,lon[,alt_m]")
    return tuple(parts)


def utcnow():
    return datetime.now(timezone.utc)
