"""Build a camera from a photo layer's attributes. Pure Python, no QGIS.

The photo layer is the source of truth: users may edit hfov, heading, pitch,
roll or height_agl in the attribute table, and the footprint follows. The
focal length in pixels is derived from hfov, so editing hfov is enough.
"""

import math
from dataclasses import dataclass
from typing import Optional

from .model import EquirectCamera, PinholeCamera


@dataclass
class CameraSetup:
    camera: object
    heading: Optional[float]
    pitch: float
    roll: float
    height_agl: float
    is_360: bool


def _num(attrs, name, default=None):
    v = attrs.get(name)
    if v is None:
        return default
    try:
        v = float(v)
    except (TypeError, ValueError):   # QGIS NULL and empty strings
        return default
    return default if math.isnan(v) else v


def camera_from_attributes(attrs: dict, default_height: float = 1.6) -> CameraSetup:
    """Raise ValueError with a user-facing reason if the attributes can't be used."""
    width = _num(attrs, "width")
    height = _num(attrs, "height")
    if not width or not height:
        raise ValueError("image width/height missing")
    is_360 = str(attrs.get("type") or "").lower() == "360"
    heading = _num(attrs, "heading")
    if heading is not None:
        heading %= 360.0

    if is_360:
        camera = EquirectCamera(int(width), int(height))
        heading = 0.0 if heading is None else heading
    else:
        hfov = _num(attrs, "hfov")
        if hfov is None or not 0 < hfov < 180:
            raise ValueError("horizontal field of view (hfov) missing or invalid")
        focal_px = (width / 2.0) / math.tan(math.radians(hfov / 2.0))
        camera = PinholeCamera(int(width), int(height), focal_px)

    h = _num(attrs, "height_agl", default_height)
    return CameraSetup(
        camera=camera,
        heading=heading,
        pitch=_num(attrs, "pitch", 0.0),
        roll=_num(attrs, "roll", 0.0),
        height_agl=h if h is not None and h >= 0 else default_height,
        is_360=is_360,
    )
