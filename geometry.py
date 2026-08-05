"""Obstruction-map pixel <-> az/el conversions.

The dish reports a square obstruction map (123x123 on current firmware) that
is an azimuthal-equidistant-style projection of the sky. Pixel conventions
follow LEOViz (clarkzjw/LEOViz, starlink/data_feature_extraction.py), whose
method was validated to ~2.4 deg mean error in arXiv:2601.13790:

- X = column index (0 = left), Y = row index (0 = top).
- Scale: DEG_PER_PX = max_theta_deg / 62 = 80/62 degrees per pixel.
- FRAME_EARTH (map_reference_frame == 1): map center is the zenith and image
  "up" is North. Azimuth is compass (0=N, 90=E), elevation = 90 - radius.
- FRAME_UT (map_reference_frame == 2): map center is the dish boresight and
  image "down" points along the boresight azimuth. The zenith therefore sits
  tilt_deg *up-image* from center. Radius from the zenith point is the zenith
  angle; atan2(dx, dy) measured from image-down equals azimuth relative to
  the boresight azimuth.

Azimuths returned are compass degrees in [0, 360); elevation in degrees.
"""

import math
from dataclasses import dataclass

FRAME_UNKNOWN = 0
FRAME_EARTH = 1
FRAME_UT = 2


@dataclass(frozen=True)
class MapGeometry:
    """Projection parameters for one obstruction map configuration."""
    n_rows: int = 123
    n_cols: int = 123
    max_theta_deg: float = 80.0

    @property
    def center(self) -> int:
        # LEOViz convention: 62 for the 123-wide grid.
        return self.n_cols // 2 + 1

    @property
    def deg_per_px(self) -> float:
        return self.max_theta_deg / self.center


def pixel_to_azel(x, y, frame, geom: MapGeometry, tilt_deg=0.0, boresight_az_deg=0.0):
    """Convert a map pixel (X=col, Y=row, sub-pixel floats allowed) to
    (elevation_deg, azimuth_deg)."""
    c = geom.center
    s = geom.deg_per_px
    if frame == FRAME_EARTH:
        dx = x - c
        dy = (geom.n_rows - y) - c
        az = math.degrees(math.atan2(dx, dy))
    elif frame == FRAME_UT:
        cy = c - tilt_deg / s
        dx = x - c
        dy = y - cy
        az = math.degrees(math.atan2(dx, dy)) + boresight_az_deg
    else:
        raise ValueError(f"unsupported map_reference_frame {frame}")
    radius = math.hypot(dx, dy) * s
    return 90.0 - radius, (az + 360.0) % 360.0


def azel_to_pixel(el_deg, az_deg, frame, geom: MapGeometry, tilt_deg=0.0, boresight_az_deg=0.0):
    """Inverse of pixel_to_azel; returns float (x, y)."""
    c = geom.center
    s = geom.deg_per_px
    r_px = (90.0 - el_deg) / s
    if frame == FRAME_EARTH:
        a = math.radians(az_deg)
        dx = r_px * math.sin(a)
        dy = r_px * math.cos(a)
        return dx + c, geom.n_rows - (dy + c)
    elif frame == FRAME_UT:
        a = math.radians(az_deg - boresight_az_deg)
        dx = r_px * math.sin(a)
        dy = r_px * math.cos(a)
        return dx + c, dy + (c - tilt_deg / s)
    raise ValueError(f"unsupported map_reference_frame {frame}")


def _boresight_frame(el_b_deg, az_b_deg):
    """Orthonormal ENU triad for the boresight-centered projection:
    b = boresight; u1 = in the vertical plane of b, away from zenith
    (zero-roll image "down"); u2 = u1 x b (image "right", east of the
    boresight plane)."""
    el = math.radians(el_b_deg)
    az = math.radians(az_b_deg)
    b = (math.cos(el) * math.sin(az), math.cos(el) * math.cos(az), math.sin(el))
    u1 = (math.sin(el) * math.sin(az), math.sin(el) * math.cos(az), -math.cos(el))
    u2 = (u1[1] * b[2] - u1[2] * b[1],
          u1[2] * b[0] - u1[0] * b[2],
          u1[0] * b[1] - u1[1] * b[0])
    return b, u1, u2


def pixel_to_azel_bs(x, y, geom: MapGeometry, boresight_el_deg,
                     boresight_az_deg, roll_deg=0.0):
    """FRAME_UT alternative model: azimuthal-equidistant projection about the
    boresight axis. Pixel radius is the angle off boresight; the image is
    rotated by roll_deg about the axis (0 = image-down lies in the vertical
    plane of the boresight, pointing away from zenith)."""
    c = geom.center
    s = geom.deg_per_px
    dx = x - c
    dy = y - c
    theta = math.radians(math.hypot(dx, dy) * s)
    psi = math.atan2(dx, dy) + math.radians(roll_deg)
    b, u1, u2 = _boresight_frame(boresight_el_deg, boresight_az_deg)
    ct, st = math.cos(theta), math.sin(theta)
    cp, sp = math.cos(psi), math.sin(psi)
    v = tuple(ct * b[i] + st * (cp * u1[i] + sp * u2[i]) for i in range(3))
    el = math.degrees(math.asin(max(-1.0, min(1.0, v[2]))))
    az = math.degrees(math.atan2(v[0], v[1])) % 360.0
    return el, az


def azel_to_pixel_bs(el_deg, az_deg, geom: MapGeometry, boresight_el_deg,
                     boresight_az_deg, roll_deg=0.0):
    """Inverse of pixel_to_azel_bs; returns float (x, y)."""
    el = math.radians(el_deg)
    az = math.radians(az_deg)
    v = (math.cos(el) * math.sin(az), math.cos(el) * math.cos(az), math.sin(el))
    b, u1, u2 = _boresight_frame(boresight_el_deg, boresight_az_deg)
    dot = sum(v[i] * b[i] for i in range(3))
    theta = math.acos(max(-1.0, min(1.0, dot)))
    p1 = sum(v[i] * u1[i] for i in range(3))
    p2 = sum(v[i] * u2[i] for i in range(3))
    psi = math.atan2(p2, p1) - math.radians(roll_deg)
    r_px = math.degrees(theta) / geom.deg_per_px
    return (r_px * math.sin(psi) + geom.center,
            r_px * math.cos(psi) + geom.center)


def roll_from_quaternion(q, boresight_el_deg, boresight_az_deg):
    """Image roll for pixel_to_azel_bs from the dish's ned2dish quaternion.

    Empirically (see notes: obstruction-map geometry), the FRAME_UT map's
    image-down axis is the dish body -y axis; the fitted roll on real data
    matched this hypothesis to <1 deg. q = (w, x, y, z).
    """
    w, x, y, z = q
    n = math.sqrt(w * w + x * x + y * y + z * z)
    w, x, y, z = w / n, x / n, y / n, z / n
    # column 1 of the NED->dish rotation matrix = dish body y axis in NED
    dish_y_ned = (2.0 * (x * y - z * w),
                  1.0 - 2.0 * (x * x + z * z),
                  2.0 * (y * z + x * w))
    d_enu = (-dish_y_ned[1], -dish_y_ned[0], dish_y_ned[2])  # -y, NED->ENU
    _, u1, u2 = _boresight_frame(boresight_el_deg, boresight_az_deg)
    return math.degrees(math.atan2(sum(a * b for a, b in zip(d_enu, u2)),
                                   sum(a * b for a, b in zip(d_enu, u1))))


def angular_separation_deg(el1, az1, el2, az2):
    """Great-circle separation between two (elevation, azimuth) directions."""
    e1, a1 = math.radians(el1), math.radians(az1)
    e2, a2 = math.radians(el2), math.radians(az2)
    cos_sep = (math.sin(e1) * math.sin(e2) +
               math.cos(e1) * math.cos(e2) * math.cos(a1 - a2))
    return math.degrees(math.acos(max(-1.0, min(1.0, cos_sep))))


def bearing_deg(el1, az1, el2, az2):
    """Initial great-circle bearing from point 1 to point 2 on the sky dome,
    treating (az, el) like (lon, lat)."""
    e1, e2 = math.radians(el1), math.radians(el2)
    da = math.radians(az2 - az1)
    x = math.sin(da) * math.cos(e2)
    y = math.cos(e1) * math.sin(e2) - math.sin(e1) * math.cos(e2) * math.cos(da)
    return (math.degrees(math.atan2(x, y)) + 360.0) % 360.0
