"""Dish gRPC I/O for satellite matching.

Thin wrappers over starlink_grpc (imported from the sibling
starlink-grpc-tools clone) that return exactly what the matcher needs.
"""

import logging
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "starlink-grpc-tools"))
import starlink_grpc  # noqa: E402

from geometry import MapGeometry  # noqa: E402

logger = logging.getLogger(__name__)


@dataclass
class DishState:
    state: str
    tilt_deg: float
    boresight_az_deg: float
    boresight_el_deg: float
    attitude_state: str
    attitude_uncertainty_deg: float
    hardware: str
    ned2dish_q: tuple = (1.0, 0.0, 0.0, 0.0)  # (w, x, y, z)


@dataclass
class MapSample:
    t: float            # unix time when the response was received
    lit: np.ndarray     # bool (rows, cols): pixel has been used
    valid: np.ndarray   # bool (rows, cols): pixel is inside the FOV
    frame: int
    geom: MapGeometry


class Dish:
    def __init__(self, target=None):
        self.ctx = starlink_grpc.ChannelContext(target=target)

    def close(self):
        self.ctx.close()

    def get_state(self) -> DishState:
        status = starlink_grpc.get_status(self.ctx)
        align = status.alignment_stats
        if status.HasField("outage"):
            state = str(status.outage.cause)
        else:
            state = "CONNECTED"
        att_field = align.DESCRIPTOR.fields_by_name["attitude_estimation_state"]
        att_name = att_field.enum_type.values_by_number[
            align.attitude_estimation_state].name
        return DishState(
            state=state,
            tilt_deg=align.tilt_angle_deg,
            boresight_az_deg=align.boresight_azimuth_deg,
            boresight_el_deg=align.boresight_elevation_deg,
            attitude_state=att_name,
            attitude_uncertainty_deg=align.attitude_uncertainty_deg,
            hardware=status.device_info.hardware_version,
            ned2dish_q=(status.ned2dish_quaternion.q_scalar,
                        status.ned2dish_quaternion.q_x,
                        status.ned2dish_quaternion.q_y,
                        status.ned2dish_quaternion.q_z),
        )

    def get_map(self) -> MapSample:
        m = starlink_grpc.get_obstruction_map(self.ctx)
        t = time.time()
        arr = np.array(m.snr, dtype=float).reshape(m.num_rows, m.num_cols)
        return MapSample(
            t=t,
            lit=arr > 0.0,
            valid=arr >= 0.0,
            frame=int(m.map_reference_frame),
            geom=MapGeometry(n_rows=m.num_rows, n_cols=m.num_cols,
                             max_theta_deg=m.max_theta_deg or 80.0),
        )

    def reset_map(self):
        starlink_grpc.reset_obstruction_map(self.ctx)
        logger.info("Obstruction map reset")

    def try_get_location(self):
        """Return (lat, lon, alt_m) or None if disabled by policy."""
        try:
            loc = starlink_grpc.location_data(self.ctx)
            if loc.get("latitude") is not None:
                return loc["latitude"], loc["longitude"], loc.get("altitude") or 0.0
        except Exception as e:
            logger.debug("get_location unavailable: %s", e)
        return None
