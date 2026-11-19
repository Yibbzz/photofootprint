"""Work out a photo's field of view from EXIF, a sensor database or defaults.

Every result says where it came from (fov_source), so defaults are never
hidden from the user.
"""

import json
import math
import os
from dataclasses import dataclass
from typing import Optional

from .model import EquirectCamera, PinholeCamera

FULL_FRAME_DIAGONAL_MM = math.hypot(36.0, 24.0)
DEFAULT_FOCAL_35MM = 26.0   # main camera on most current phones

_DB_PATH = os.path.join(os.path.dirname(__file__), "sensors.json")
_db_cache = None


def sensor_db() -> dict:
    global _db_cache
    if _db_cache is None:
        with open(_DB_PATH, encoding="utf-8") as f:
            raw = json.load(f)["sensors"]
        _db_cache = {k.lower(): v for k, v in raw.items()}
    return _db_cache


def sensor_width_mm(make: Optional[str], model: Optional[str]) -> Optional[float]:
    if not model:
        return None
    db = sensor_db()
    for key in (f"{make or ''}|{model}".lower(), f"|{model}".lower()):
        if key in db:
            return float(db[key])
    return None


@dataclass
class Intrinsics:
    camera: object          # PinholeCamera or EquirectCamera
    fov_source: str         # "360", "focal+sensor", "focal_plane_res", "35mm", "default"
    sensor_w_mm: Optional[float] = None

    @property
    def hfov(self):
        return self.camera.hfov

    @property
    def vfov(self):
        return self.camera.vfov

    @property
    def focal_px(self):
        return getattr(self.camera, "focal_px", None)


def resolve(md, default_focal_35mm: float = DEFAULT_FOCAL_35MM) -> Intrinsics:
    """Build the camera model for a PhotoMetadata record."""
    w, h = md.width, md.height
    if not w or not h:
        raise ValueError("image size unknown")

    if md.projection == "360":
        return Intrinsics(EquirectCamera(w, h), "360")

    long_side = max(w, h)

    # 1. Real focal length and a known sensor width
    sensor_w = sensor_width_mm(md.make, md.model)
    if md.focal_mm and sensor_w:
        return Intrinsics(PinholeCamera(w, h, md.focal_mm * long_side / sensor_w),
                          "focal+sensor", sensor_w)

    # 2. Real focal length and the focal plane resolution tag (pixels per unit)
    if md.focal_mm and md.focal_plane_x_res and md.focal_plane_unit_mm:
        px_per_mm = md.focal_plane_x_res / md.focal_plane_unit_mm
        if px_per_mm > 0:
            sensor_w = long_side / px_per_mm
            return Intrinsics(PinholeCamera(w, h, md.focal_mm * px_per_mm),
                              "focal_plane_res", sensor_w)

    # 3. 35 mm equivalent, defined on the diagonal
    diag = math.hypot(w, h)
    if md.focal_35mm:
        cam = PinholeCamera(w, h, md.focal_35mm * diag / FULL_FRAME_DIAGONAL_MM)
        sensor_w = md.focal_mm * long_side / cam.focal_px if md.focal_mm else None
        return Intrinsics(cam, "35mm", sensor_w)

    # 4. Default
    return Intrinsics(PinholeCamera(w, h, default_focal_35mm * diag / FULL_FRAME_DIAGONAL_MM),
                      "default")
