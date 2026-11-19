"""The radial sweep must match exact line of sight, in both directions.

This is the check that showed gdal.ViewshedGenerate hiding 5-11 % of
genuinely visible ground.
"""

import numpy as np
import pytest

from photofootprint.footprint.compute import prepare
from photofootprint.terrain.raycast import line_of_sight

from test_footprint import X0, Y0, make_dem

BUILDINGS = [(-10, 50, 10, 70, 10.0), (25, 20, 40, 35, 6.0), (-60, 90, -30, 100, 15.0),
             (-20, -80, 30, -60, 4.0), (70, -40, 75, 40, 1.2)]   # last one is a low wall


def hills():
    dem = make_dem(buildings=BUILDINGS)
    n = 1200
    yy, xx = np.mgrid[0:n, 0:n] * 1.0
    z = dem.band.ReadAsArray().astype(float)
    z += 6 * np.sin(xx / 37.0) * np.cos(yy / 53.0) + 0.02 * xx
    dem.band.WriteArray(z.astype(np.float32))
    return dem


@pytest.mark.parametrize("make", [lambda: make_dem(buildings=BUILDINGS), hills], ids=["buildings", "hills"])
def test_sweep_matches_exact_line_of_sight(make):
    prep = prepare(make(), X0, Y0, 1.6, 250.0)
    win = prep.window
    xs, ys = win.cell_centres()
    cam = np.array([X0, Y0, prep.camera_z])
    rows, cols = np.nonzero(prep.in_range & (prep.dist > 3))
    pick = np.random.default_rng(11).choice(len(rows), 3000, replace=False)
    wrongly_hidden = wrongly_seen = seen = 0
    for i in pick:
        r, c = rows[i], cols[i]
        exact = line_of_sight(win, cam, [xs[c], ys[r], win.z[r, c] + 0.1])
        got = bool(prep.seen_from_camera[r, c])
        seen += exact
        wrongly_hidden += exact and not got
        wrongly_seen += got and not exact
    print(f"exact seen {seen}, wrongly hidden {wrongly_hidden}, wrongly seen {wrongly_seen} of {len(pick)}")
    assert 300 < seen < 2700            # a real mix of seen and hidden points
    assert wrongly_hidden / len(pick) < 0.01
    assert wrongly_seen / len(pick) < 0.01
