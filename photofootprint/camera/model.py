"""Camera models: pixel <-> 3D ray. NumPy only, no QGIS imports.

World frame: x = east, y = north, z = up, in metres (a projected CRS).
Camera frame: x = right, y = forward (optical axis), z = up.

Pose angles, in degrees:
  heading  clockwise from north (0 = looking north, 90 = east)
  pitch    positive = looking up
  roll     positive = camera rotated clockwise as seen from behind
           (the right-hand side of the image drops)

Pixel coordinates are continuous: (0, 0) is the top-left corner of the
top-left pixel, and (width, height) is the bottom-right corner. The centre
of pixel (i, j) is (i + 0.5, j + 0.5).
"""

import math
from dataclasses import dataclass

import numpy as np


def rotation(heading: float, pitch: float, roll: float) -> np.ndarray:
    """3x3 matrix taking camera-frame vectors to world-frame vectors."""
    h, p, r = (math.radians(a) for a in (heading, pitch, roll))
    ch, sh = math.cos(h), math.sin(h)
    cp, sp = math.cos(p), math.sin(p)
    cr, sr = math.cos(r), math.sin(r)
    rz = np.array([[ch, sh, 0.0], [-sh, ch, 0.0], [0.0, 0.0, 1.0]])   # turn clockwise by heading
    rx = np.array([[1.0, 0.0, 0.0], [0.0, cp, -sp], [0.0, sp, cp]])   # tilt up by pitch
    ry = np.array([[cr, 0.0, sr], [0.0, 1.0, 0.0], [-sr, 0.0, cr]])   # roll about the optical axis
    return rz @ rx @ ry


@dataclass(frozen=True)
class Pose:
    x: float
    y: float
    z: float
    heading: float = 0.0
    pitch: float = 0.0
    roll: float = 0.0

    @property
    def centre(self) -> np.ndarray:
        return np.array([self.x, self.y, self.z], dtype=float)

    @property
    def matrix(self) -> np.ndarray:
        return rotation(self.heading, self.pitch, self.roll)


@dataclass(frozen=True)
class PinholeCamera:
    """Perspective camera. focal_px is the focal length in pixels."""
    width: int
    height: int
    focal_px: float
    cx: float = None
    cy: float = None

    def __post_init__(self):
        if self.cx is None:
            object.__setattr__(self, "cx", self.width / 2.0)
        if self.cy is None:
            object.__setattr__(self, "cy", self.height / 2.0)

    @property
    def hfov(self) -> float:
        return math.degrees(2 * math.atan(self.width / 2.0 / self.focal_px))

    @property
    def vfov(self) -> float:
        return math.degrees(2 * math.atan(self.height / 2.0 / self.focal_px))

    def ray_cam(self, u, v) -> np.ndarray:
        u, v = np.broadcast_arrays(np.asarray(u, float), np.asarray(v, float))
        d = np.stack([u - self.cx, np.full(u.shape, self.focal_px), self.cy - v], axis=-1)
        return d / np.linalg.norm(d, axis=-1, keepdims=True)

    def project_cam(self, d):
        d = np.asarray(d, float)
        fwd = d[..., 1]
        ahead = fwd > 1e-12
        safe = np.where(ahead, fwd, 1.0)
        u = self.cx + self.focal_px * d[..., 0] / safe
        v = self.cy - self.focal_px * d[..., 2] / safe
        inside = ahead & (u >= 0) & (u <= self.width) & (v >= 0) & (v <= self.height)
        return u, v, inside


@dataclass(frozen=True)
class EquirectCamera:
    """360 camera. The image centre looks along the pose heading at the horizon."""
    width: int
    height: int

    hfov = 360.0
    vfov = 180.0

    def ray_cam(self, u, v) -> np.ndarray:
        u, v = np.broadcast_arrays(np.asarray(u, float), np.asarray(v, float))
        yaw = (u / self.width - 0.5) * 2 * math.pi
        pitch = (0.5 - v / self.height) * math.pi
        cp = np.cos(pitch)
        return np.stack([cp * np.sin(yaw), cp * np.cos(yaw), np.sin(pitch)], axis=-1)

    def project_cam(self, d):
        d = np.asarray(d, float)
        n = np.linalg.norm(d, axis=-1)
        yaw = np.arctan2(d[..., 0], d[..., 1])
        pitch = np.arcsin(np.clip(d[..., 2] / np.where(n > 0, n, 1.0), -1, 1))
        u = (yaw / (2 * math.pi) + 0.5) * self.width
        v = (0.5 - pitch / math.pi) * self.height
        return u, v, n > 0


def pixel_to_ray(camera, pose: Pose, u, v):
    """Unit world-frame direction(s) for pixel(s) (u, v). Origin is pose.centre."""
    return camera.ray_cam(u, v) @ pose.matrix.T


def point_to_pixel(camera, pose: Pose, points):
    """Project world point(s) of shape (..., 3) into the image.

    Returns (u, v, in_frame). in_frame is False for points behind the camera
    or outside the image. Occlusion is not checked here.
    """
    d = np.asarray(points, float) - pose.centre
    return camera.project_cam(d @ pose.matrix)
