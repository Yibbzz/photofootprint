"""Ray-surface intersection and line of sight on a DEM window. NumPy only.

Surface heights between cell centres are interpolated bilinearly, so rays
hit a smooth surface rather than a staircase of cells.
"""

import math

import numpy as np

from .dem import Window

LOS_TOLERANCE_M = 0.25   # ignore tiny surface bumps from interpolation on slopes


def sample(win: Window, x, y):
    """Bilinear surface height at (x, y) arrays. NaN outside the window or on no-data."""
    x = np.asarray(x, float)
    y = np.asarray(y, float)
    fc = (x - win.gt[0]) / win.gt[1] - 0.5     # fractional column of cell centres
    fr = (y - win.gt[3]) / win.gt[5] - 0.5
    rows, cols = win.z.shape
    c0 = np.floor(fc).astype(int)
    r0 = np.floor(fr).astype(int)
    tc = fc - c0
    tr = fr - r0
    # Clamp at the edges so points within half a cell of the border still work
    c0c, c1c = np.clip(c0, 0, cols - 1), np.clip(c0 + 1, 0, cols - 1)
    r0c, r1c = np.clip(r0, 0, rows - 1), np.clip(r0 + 1, 0, rows - 1)
    z = win.z
    out = ((z[r0c, c0c] * (1 - tc) + z[r0c, c1c] * tc) * (1 - tr)
           + (z[r1c, c0c] * (1 - tc) + z[r1c, c1c] * tc) * tr)
    outside = (fc < -0.5) | (fr < -0.5) | (fc > cols - 0.5) | (fr > rows - 0.5)
    return np.where(outside, np.nan, out)


def first_hit(win: Window, origin, direction, max_range: float):
    """First point where the ray meets the surface, or None.

    max_range limits the horizontal distance travelled. Returns
    (point (3,), horizontal distance).
    """
    o = np.asarray(origin, float)
    d = np.asarray(direction, float)
    d = d / np.linalg.norm(d)
    horiz = math.hypot(d[0], d[1])
    t_max = min(max_range / horiz if horiz > 1e-9 else math.inf, 2 * max_range + 100.0)
    step = 0.5 * win.res
    ts = np.arange(step, t_max + step, step)
    pts = o + ts[:, None] * d
    above = pts[:, 2] - sample(win, pts[:, 0], pts[:, 1])
    below = np.nonzero(above <= 0)[0]          # NaN compares False: no-data never hits
    if below.size == 0:
        return None
    i = below[0]
    lo, hi = (ts[i - 1] if i > 0 else 0.0), ts[i]
    for _ in range(30):                        # bisection to millimetres
        mid = 0.5 * (lo + hi)
        p = o + mid * d
        h = p[2] - float(sample(win, p[0], p[1]))
        if h > 0:
            lo = mid
        else:
            hi = mid
    p = o + hi * d
    return p, math.hypot(p[0] - o[0], p[1] - o[1])


def line_of_sight(win: Window, a, b) -> bool:
    """True if nothing on the surface rises above the straight line from a to b.

    The last cell before b is ignored, so a target isn't hidden by its own
    neighbouring cells on a slope or at the foot of a wall.
    """
    a = np.asarray(a, float)
    b = np.asarray(b, float)
    horiz = math.hypot(b[0] - a[0], b[1] - a[1])
    end = horiz - win.res
    if end <= 0:
        return True
    step = 0.5 * win.res
    s = np.arange(step, end, step) / horiz      # fractions along the line
    pts = a + s[:, None] * (b - a)
    z = sample(win, pts[:, 0], pts[:, 1])
    blocked = z > pts[:, 2] + LOS_TOLERANCE_M
    return not bool(np.any(blocked))            # NaN (no data) never blocks
