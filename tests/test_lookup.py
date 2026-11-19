import math

import numpy as np
import pytest

from photofootprint.camera.attrs import CameraSetup
from photofootprint.camera.model import EquirectCamera, Pose, point_to_pixel
from photofootprint.footprint.compute import frame_mask, prepare
from photofootprint.lookup.rank import PhotoAt, find_photos, image_to_ground
from photofootprint.terrain.dem import Window
from photofootprint.terrain.raycast import first_hit, line_of_sight, sample

from test_footprint import CAM, GROUND, X0, Y0, make_dem

BUILDING = (-10, 50, 10, 70, 10.0)   # 50-70 m north of the camera


def make_setup(heading, pitch=0.0, camera=CAM, height=1.6):
    return CameraSetup(camera, heading, pitch, 0.0, height, isinstance(camera, EquirectCamera))


def window(dem, radius=400):
    return dem.window(X0, Y0, radius)


def test_bilinear_sample_is_exact_on_a_plane():
    n = 50
    xs = np.arange(n) + 0.5
    ys = 100 - (np.arange(n) + 0.5)
    gx, gy = np.meshgrid(xs, ys)
    win = Window(3.0 + 0.2 * gx - 0.1 * gy, (0.0, 1.0, 0.0, 100.0, 0.0, -1.0), "")
    px, py = np.array([10.3, 20.71, 33.0]), np.array([70.2, 81.5, 60.9])
    assert np.allclose(sample(win, px, py), 3.0 + 0.2 * px - 0.1 * py)
    assert np.isnan(sample(win, [-5.0], [70.0]))[0]


def test_ray_hits_flat_ground_where_trigonometry_says():
    win = window(make_dem())
    c = np.array([X0, Y0, GROUND + 1.6])
    d = np.array([0.0, math.cos(math.radians(10)), -math.sin(math.radians(10))])
    p, dist = first_hit(win, c, d, 300)
    assert dist == pytest.approx(1.6 / math.tan(math.radians(10)), abs=0.005)
    assert p[2] == pytest.approx(GROUND, abs=0.005)


def test_level_ray_hits_building_wall():
    win = window(make_dem(buildings=[BUILDING]))
    p, dist = first_hit(win, [X0, Y0, GROUND + 1.6], [0, 1, 0], 300)
    assert 49.0 < p[1] - Y0 < 50.5      # the wall face (smoothed over one cell)


def test_ray_above_horizon_misses():
    assert first_hit(window(make_dem()), [X0, Y0, GROUND + 1.6], [0, 1, 0.05], 300) is None


def test_line_of_sight():
    win = window(make_dem(buildings=[BUILDING]))
    cam = [X0, Y0, GROUND + 1.6]
    assert line_of_sight(win, cam, [X0, Y0 + 40, GROUND + 0.1])
    assert not line_of_sight(win, cam, [X0, Y0 + 120, GROUND + 0.1])
    assert line_of_sight(win, cam, [X0 + 60, Y0 + 120, GROUND + 0.1])   # off to the side


@pytest.mark.parametrize("heading,pitch", [(0, -8), (135, -3), (250, 0)])
def test_image_click_lands_on_the_ground_point_it_shows(heading, pitch):
    dem = make_dem()
    # Make the terrain a gentle slope so it's not trivially flat
    xs = np.arange(1200) * 1.0
    dem.band.WriteArray((GROUND + 0.03 * xs[None, :] + 0.0 * xs[:, None]).astype(np.float32))
    win = window(dem)
    s = make_setup(heading, pitch)
    ground = win.lowest_near(X0, Y0, 2.0)
    pose = Pose(X0, Y0, ground + 1.6, heading, pitch, 0)
    rng = np.random.default_rng(3)
    for _ in range(20):
        ang = math.radians(heading + rng.uniform(-25, 25))
        r = rng.uniform(15, 150)
        tx, ty = X0 + r * math.sin(ang), Y0 + r * math.cos(ang)
        tz = float(sample(win, tx, ty))
        u, v, ok = point_to_pixel(CAM, pose, [tx, ty, tz])
        if not ok:
            continue
        p, _ = image_to_ground(win, s, X0, Y0, float(u), float(v), 300)
        assert math.hypot(p[0] - tx, p[1] - ty) < 0.05


def test_find_photos_keeps_only_those_that_see_the_point():
    win = window(make_dem(buildings=[BUILDING]))
    photos = [
        PhotoAt("facing", make_setup(0), X0 + 40, Y0),            # sees it, nothing in the way
        PhotoAt("away", make_setup(180), X0 + 40, Y0),           # facing the wrong way
        PhotoAt("blocked", make_setup(0), X0, Y0),               # building in between
        PhotoAt("too_far", make_setup(0), X0 + 40, Y0 - 400),    # beyond max range
        PhotoAt("no_heading", make_setup(None), X0 + 40, Y0),
    ]
    result = find_photos(win, X0, Y0 + 120, photos, max_range=300)
    assert [m.key for m in result] == ["facing"]
    assert result[0].distance_m == pytest.approx(math.hypot(40, 120))


def test_ranking_prefers_closer_and_more_central():
    win = window(make_dem())
    tx, ty = X0, Y0 + 100
    photos = [
        PhotoAt("far_centred", make_setup(0), X0, Y0 - 100),
        PhotoAt("near_centred", make_setup(0), X0, Y0 + 20),
        PhotoAt("near_edge", make_setup(25), X0, Y0 + 20),       # point near the left edge
    ]
    keys = [m.key for m in find_photos(win, tx, ty, photos, max_range=300)]
    assert keys == ["near_centred", "near_edge", "far_centred"]


def test_360_photo_sees_point_in_any_direction():
    win = window(make_dem())
    photos = [PhotoAt("pano", make_setup(0, camera=EquirectCamera(8000, 4000)), X0, Y0)]
    result = find_photos(win, X0 - 50, Y0 - 50, photos, max_range=300)
    assert len(result) == 1 and result[0].off_centre == 0.0


def test_lookup_agrees_with_footprints():
    """Footprints (radial sweep) and lookup (line of sight) are separate code
    paths. For points inside the photo's frame they must agree on visibility."""
    dem = make_dem(buildings=[BUILDING, (25, 20, 40, 35, 6.0), (-60, 90, -30, 100, 15.0)])
    s = make_setup(15, -2)
    prep = prepare(dem, X0, Y0, 1.6, 200.0)
    raw = frame_mask(prep, CAM, 15, -2)
    seen = raw & prep.seen_from_camera
    win = prep.window
    rows, cols = np.nonzero(raw & (prep.dist > 3))
    pick = np.random.default_rng(4).choice(len(rows), 1500, replace=False)
    xs, ys = win.cell_centres()
    agree = n_seen = 0
    for i in pick:
        r, c = rows[i], cols[i]
        found = bool(find_photos(win, xs[c], ys[r], [PhotoAt(1, s, X0, Y0)], 200.0))
        agree += found == bool(seen[r, c])
        n_seen += found
    assert 200 < n_seen < 1300          # a real mix of seen and hidden points in the frame
    assert agree / len(pick) > 0.98
