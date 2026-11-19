import math

import numpy as np
import pytest

from photofootprint.camera.model import (
    EquirectCamera, PinholeCamera, Pose, pixel_to_ray, point_to_pixel, rotation,
)


def test_heading_turns_clockwise_from_north():
    fwd = np.array([0.0, 1.0, 0.0])
    assert np.allclose(rotation(0, 0, 0) @ fwd, [0, 1, 0])
    assert np.allclose(rotation(90, 0, 0) @ fwd, [1, 0, 0])      # east
    assert np.allclose(rotation(180, 0, 0) @ fwd, [0, -1, 0])    # south


def test_pitch_up_and_roll_drops_right_side():
    fwd, right = np.array([0.0, 1.0, 0.0]), np.array([1.0, 0.0, 0.0])
    assert np.allclose(rotation(0, 30, 0) @ fwd, [0, math.cos(math.radians(30)), 0.5])
    assert (rotation(0, 0, 10) @ right)[2] < 0


def test_rotation_is_orthonormal():
    r = rotation(123.4, -17.0, 6.5)
    assert np.allclose(r @ r.T, np.eye(3))
    assert np.isclose(np.linalg.det(r), 1.0)


def test_centre_pixel_looks_along_heading_and_pitch():
    cam = PinholeCamera(4000, 3000, 3000.0)
    pose = Pose(0, 0, 0, heading=60, pitch=-10)
    d = pixel_to_ray(cam, pose, 2000, 1500)
    h, p = math.radians(60), math.radians(-10)
    assert np.allclose(d, [math.cos(p) * math.sin(h), math.cos(p) * math.cos(h), math.sin(p)])


def test_fov_matches_focal_length():
    # 18 mm lens on a 36 mm wide sensor = 90 degrees horizontal
    cam = PinholeCamera(6000, 4000, 18 * 6000 / 36)
    assert cam.hfov == pytest.approx(90.0)


@pytest.mark.parametrize("pose", [
    Pose(500000, 200000, 55, heading=0),
    Pose(500000, 200000, 55, heading=237.5, pitch=-12, roll=3),
    Pose(-10, 4, 0, heading=359, pitch=40, roll=-20),
])
def test_pinhole_round_trip_under_half_pixel(pose):
    cam = PinholeCamera(4032, 3024, 3028.6)
    rng = np.random.default_rng(1)
    u = rng.uniform(0, 4032, 500)
    v = rng.uniform(0, 3024, 500)
    dist = rng.uniform(1, 500, 500)[:, None]
    pts = pose.centre + dist * pixel_to_ray(cam, pose, u, v)
    u2, v2, ok = point_to_pixel(cam, pose, pts)
    assert ok.all()
    assert np.max(np.hypot(u2 - u, v2 - v)) < 0.5


def test_equirect_round_trip_under_half_pixel():
    cam = EquirectCamera(8000, 4000)
    pose = Pose(0, 0, 2, heading=75, pitch=5, roll=2)
    rng = np.random.default_rng(2)
    u = rng.uniform(1, 7999, 500)
    v = rng.uniform(1, 3999, 500)
    pts = pose.centre + 20 * pixel_to_ray(cam, pose, u, v)
    u2, v2, ok = point_to_pixel(cam, pose, pts)
    assert ok.all()
    assert np.max(np.hypot(u2 - u, v2 - v)) < 0.5


def test_equirect_centre_looks_along_heading():
    cam = EquirectCamera(8000, 4000)
    d = pixel_to_ray(cam, Pose(0, 0, 0, heading=90), 4000, 2000)
    assert np.allclose(d, [1, 0, 0])


def test_points_behind_or_outside_frame_are_not_visible():
    cam = PinholeCamera(4000, 3000, 3000.0)
    pose = Pose(0, 0, 0, heading=0)
    _, _, ok = point_to_pixel(cam, pose, [[0, -10, 0], [100, 10, 0], [0, 10, 0]])
    assert list(ok) == [False, False, True]


def test_flat_ground_near_edge_matches_trigonometry():
    """Bottom-centre ray from 1.6 m, pitched down 10 deg, hits the ground at 1.6 / tan(10 + vfov/2)."""
    cam = PinholeCamera(4000, 3000, 3000.0)
    pose = Pose(0, 0, 1.6, heading=0, pitch=-10)
    d = pixel_to_ray(cam, pose, 2000, 3000)
    t = -pose.z / d[2]
    expected = 1.6 / math.tan(math.radians(10 + cam.vfov / 2))
    assert t * d[1] == pytest.approx(expected, rel=1e-9)
