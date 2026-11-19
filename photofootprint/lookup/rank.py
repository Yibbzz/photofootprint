"""Which photos show a place, best first. NumPy only, no QGIS.

A photo shows a target point when the point projects inside its frame and
nothing on the surface model blocks the straight line from the camera.
The heading and pitch come from the photo layer as they are now, so a
corrected heading takes effect immediately.

Ranking: estimated ground detail (cm per pixel at that distance), penalised
the further the point is from the centre of the frame. Points near the edge
are the first to drop out of the photo when the compass is a few degrees out.
"""

import math
from dataclasses import dataclass
from typing import Any, List, Optional

import numpy as np

from ..camera.model import EquirectCamera, Pose, pixel_to_ray, point_to_pixel
from ..terrain.dem import Dem, Window
from ..terrain.raycast import first_hit, line_of_sight

GROUND_SEARCH_M = 2.0   # must match footprint.compute


@dataclass
class PhotoAt:
    key: Any               # whatever identifies the photo to the caller (e.g. feature id)
    setup: Any             # camera.attrs.CameraSetup
    x: float
    y: float


@dataclass
class Match:
    key: Any
    u: float               # pixel in the photo
    v: float
    distance_m: float      # horizontal distance camera -> target
    gsd_cm: float          # estimated centimetres per pixel at the target
    off_centre: float      # 0 = image centre, 1 = image corner (0 for 360 photos)
    score: float           # lower is better


def camera_centre(win: Window, x, y, height_agl, dtm: Optional[Dem] = None):
    """Camera position (3,), using the same ground rule as the footprints."""
    ground = None
    if dtm is not None:
        try:
            ground = dtm.window(x, y, 2 * dtm.res).value_at(x, y)
        except ValueError:
            ground = None
    if ground is None:
        ground = win.lowest_near(x, y, max(GROUND_SEARCH_M, win.res))
    if ground is None:
        return None
    return np.array([x, y, ground + height_agl])


LOS_TARGET_OFFSET_M = 0.1   # line of sight aims just above the surface


def surface_point(win: Window, x, y):
    """Target on the surface model: the cell's own height (a roof stays a roof)."""
    z = win.value_at(x, y)
    return None if z is None else np.array([x, y, z])


def find_photos(win: Window, tx: float, ty: float, photos: List[PhotoAt],
                max_range: float, dtm: Optional[Dem] = None) -> List[Match]:
    target = surface_point(win, tx, ty)
    if target is None:
        return []
    matches = []
    for p in photos:
        s = p.setup
        if s.heading is None:
            continue
        dist = math.hypot(tx - p.x, ty - p.y)
        if dist > max_range or not win.contains(p.x, p.y):
            continue
        c = camera_centre(win, p.x, p.y, s.height_agl, dtm)
        if c is None:
            continue
        pose = Pose(c[0], c[1], c[2], s.heading, s.pitch, s.roll)
        u, v, ok = point_to_pixel(s.camera, pose, target)
        aim = target + np.array([0.0, 0.0, LOS_TARGET_OFFSET_M])
        if not bool(ok) or not line_of_sight(win, c, aim):
            continue
        cam = s.camera
        d3 = float(np.linalg.norm(target - c))
        if isinstance(cam, EquirectCamera):
            focal = cam.width / (2 * math.pi)
            off = 0.0
        else:
            focal = cam.focal_px
            off = math.hypot((float(u) - cam.cx) / (cam.width / 2),
                             (float(v) - cam.cy) / (cam.height / 2)) / math.sqrt(2)
        gsd = 100.0 * d3 / focal
        matches.append(Match(p.key, float(u), float(v), dist, gsd, off, gsd * (1.0 + off)))
    matches.sort(key=lambda m: m.score)
    return matches


def image_to_ground(win: Window, setup, x, y, u, v, max_range, dtm: Optional[Dem] = None):
    """Where the ray through pixel (u, v) meets the surface: (point (3,), distance) or None."""
    c = camera_centre(win, x, y, setup.height_agl, dtm)
    if c is None or setup.heading is None:
        return None
    pose = Pose(c[0], c[1], c[2], setup.heading, setup.pitch, setup.roll)
    d = pixel_to_ray(setup.camera, pose, u, v)
    return first_hit(win, c, d, max_range)
