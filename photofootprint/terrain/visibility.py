"""Which cells can a camera see? Radial sweep, NumPy only.

Replaces gdal.ViewshedGenerate, which was measured hiding 5-11 % of
genuinely visible ground (all four GDAL modes, always on the hidden side)
when checked against exact line of sight. See tests/test_visibility.py.

Method: cast rays outward from the camera at about two rays per cell at
max range, step along each in half-cell steps sampling the bilinear
surface, and keep the running maximum of the horizon (slope from the
camera). A sample is visible if its own slope reaches that horizon. Each
cell is visible if any sample inside it is. This is the same geometry as
raycast.line_of_sight, so the footprints and the photo lookup agree.
"""

import numpy as np

from .dem import Window
from .raycast import LOS_TOLERANCE_M, sample

TARGET_OFFSET_M = 0.1      # a point counts as seen if just above the surface is
RAYS_PER_CELL = 2.0        # ray spacing at max range, in cells
CHUNK_RAYS = 256           # rays processed together (bounds memory use)
N_SECTORS = 32             # the circle is swept in sectors, on demand


def sector_of(azimuth_rad):
    """Sector index (0..N_SECTORS-1) of azimuths in radians, clockwise from north."""
    return (np.floor(np.mod(azimuth_rad, 2 * np.pi) / (2 * np.pi) * N_SECTORS).astype(int)
            % N_SECTORS)


def sweep(win: Window, x: float, y: float, camera_z: float, max_range: float,
          sectors, seen: np.ndarray) -> None:
    """Mark cells visible from (x, y, camera_z) in `seen`, for the given sectors only."""
    res = win.res
    step = 0.5 * res
    ts = np.arange(step, max_range + step, step)          # horizontal distances
    per_sector = int(np.ceil(2 * np.pi * max_range / res * RAYS_PER_CELL / N_SECTORS))
    width = 2 * np.pi / N_SECTORS
    skip = 2   # like line_of_sight, ignore the last cell (2 samples) before each point
    rows, cols = win.z.shape
    for k in sectors:
        azimuths = k * width + (np.arange(per_sector) + 0.5) * (width / per_sector)
        for start in range(0, per_sector, CHUNK_RAYS):
            az = azimuths[start:start + CHUNK_RAYS]
            px = x + np.sin(az)[:, None] * ts[None, :]
            py = y + np.cos(az)[:, None] * ts[None, :]
            z = sample(win, px, py)

            # Slope of each blocking point as seen from the camera. The tolerance is
            # applied at the blocker, exactly as line_of_sight does.
            horizon = (z - LOS_TOLERANCE_M - camera_z) / ts
            horizon = np.where(np.isnan(horizon), -np.inf, horizon)
            running = np.maximum.accumulate(horizon, axis=1)
            before = np.full_like(running, -np.inf)
            before[:, skip:] = running[:, :-skip]         # highest horizon before this point
            target = (z + TARGET_OFFSET_M - camera_z) / ts
            vis = (target >= before) & ~np.isnan(z)

            c = np.floor((px - win.gt[0]) / win.gt[1]).astype(int)
            r = np.floor((py - win.gt[3]) / win.gt[5]).astype(int)
            ok = vis & (c >= 0) & (c < cols) & (r >= 0) & (r < rows)
            seen[r[ok], c[ok]] = True


def visible_cells(win: Window, x: float, y: float, camera_z: float, max_range: float) -> np.ndarray:
    """Bool array over the window: cells with some part visible (full circle)."""
    seen = np.zeros(win.z.shape, dtype=bool)
    sweep(win, x, y, camera_z, max_range, range(N_SECTORS), seen)
    return seen
