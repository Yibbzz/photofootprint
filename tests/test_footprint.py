import math

import numpy as np
import pytest
from osgeo import gdal, ogr, osr

from photofootprint.camera.model import EquirectCamera, PinholeCamera, Pose, pixel_to_ray
from photofootprint.footprint.compute import footprint, prepare, timed_footprint
from photofootprint.terrain.dem import Dem

GROUND = 50.0
X0, Y0 = 500000.0, 200000.0     # camera position (centre of the synthetic DEM)

# 4:3 camera with a 60 degree horizontal field of view
CAM = PinholeCamera(4000, 3000, 2000 / math.tan(math.radians(30)))


def make_dem(size_m=1200, res=1.0, buildings=(), nodata=None):
    n = int(size_m / res)
    z = np.full((n, n), GROUND, dtype=np.float32)
    ds = gdal.GetDriverByName("MEM").Create("", n, n, 1, gdal.GDT_Float32)
    left, top = X0 - size_m / 2, Y0 + size_m / 2
    ds.SetGeoTransform((left, res, 0.0, top, 0.0, -res))
    srs = osr.SpatialReference()
    srs.ImportFromEPSG(27700)
    ds.SetProjection(srs.ExportToWkt())
    for (x0, y0, x1, y1, h) in buildings:   # metres relative to the camera
        c0, c1 = int((X0 + x0 - left) / res), int((X0 + x1 - left) / res)
        r0, r1 = int((top - (Y0 + y1)) / res), int((top - (Y0 + y0)) / res)
        z[r0:r1, c0:c1] = GROUND + h
    if nodata is not None:
        ds.GetRasterBand(1).SetNoDataValue(nodata)
    ds.GetRasterBand(1).WriteArray(z)
    return Dem(ds)


def test_flat_ground_level_wedge_matches_analytic_area():
    """Level camera at 1.6 m: a 60 degree sector out to 300 m, minus the
    triangle in front of the bottom edge of the frame."""
    prep = prepare(make_dem(), X0, Y0, 1.6, 300.0)
    fp = footprint(prep, CAM, heading=0, pitch=0)
    a = math.radians(30)
    near = 1.6 / math.tan(math.radians(CAM.vfov / 2))
    expected = a * 300.0 ** 2 - near ** 2 * math.tan(a)
    assert fp.raw_area_m2 == pytest.approx(expected, rel=0.01)
    assert fp.visible_area_m2 == pytest.approx(fp.raw_area_m2)   # flat: nothing blocks
    assert fp.capped


def test_tilted_down_camera_matches_analytic_trapezoid():
    """Camera 40 m up, pitched down 50 degrees: all four frame corners hit flat
    ground, so the footprint is the quadrilateral through those hits."""
    prep = prepare(make_dem(), X0, Y0, 40.0, 500.0)
    pose = Pose(X0, Y0, GROUND + 40.0, heading=30, pitch=-50)
    fp = footprint(prep, CAM, heading=30, pitch=-50)

    corners = pixel_to_ray(CAM, pose, [0, 4000, 4000, 0], [0, 0, 3000, 3000])
    t = -40.0 / corners[:, 2]
    xs = X0 + t * corners[:, 0]
    ys = Y0 + t * corners[:, 1]
    expected = 0.5 * abs(np.dot(xs, np.roll(ys, -1)) - np.dot(ys, np.roll(xs, -1)))
    assert fp.raw_area_m2 == pytest.approx(expected, rel=0.01)
    assert not fp.capped


def test_heading_rotates_footprint():
    prep = prepare(make_dem(), X0, Y0, 1.6, 200.0)
    east = ogr.CreateGeometryFromWkt(footprint(prep, CAM, heading=90, pitch=0).visible_wkt)
    c = east.Centroid()
    assert c.GetX() - X0 > 50 and abs(c.GetY() - Y0) < 5


def test_building_blocks_view_behind_it():
    # 20 m wide, 10 m tall building 50-70 m north of the camera
    dem = make_dem(buildings=[(-10, 50, 10, 70, 10.0)])
    prep = prepare(dem, X0, Y0, 1.6, 300.0)
    north = footprint(prep, CAM, heading=0, pitch=0)
    south = footprint(prep, CAM, heading=180, pitch=0)

    vis = ogr.CreateGeometryFromWkt(north.visible_wkt)
    raw = ogr.CreateGeometryFromWkt(north.raw_wkt)
    behind = ogr.CreateGeometryFromWkt(f"POINT ({X0} {Y0 + 150})")
    in_front = ogr.CreateGeometryFromWkt(f"POINT ({X0} {Y0 + 30})")
    assert raw.Contains(behind) and not vis.Contains(behind)
    assert vis.Contains(in_front)
    assert north.visible_pct < 95
    assert south.visible_pct == pytest.approx(100.0)


def test_polygon_area_matches_cell_count():
    dem = make_dem(buildings=[(-10, 50, 10, 70, 10.0)])
    fp = footprint(prepare(dem, X0, Y0, 1.6, 300.0), CAM, heading=10, pitch=-3)
    assert ogr.CreateGeometryFromWkt(fp.visible_wkt).GetArea() == pytest.approx(fp.visible_area_m2)


def test_camera_on_a_roof_cell_starts_at_ground_level():
    # Photo point 1 m inside a building: GPS put the camera on the roof
    dem = make_dem(buildings=[(-1, -10, 20, 10, 8.0)])
    prep = prepare(dem, X0, Y0, 1.6, 100.0)
    assert prep.ground_z == pytest.approx(GROUND)
    assert prep.ground_source == "dsm_lowest_2m"


def test_dtm_gives_ground_height():
    dsm = make_dem(buildings=[(-30, -30, 30, 30, 8.0)])
    dtm = make_dem()
    prep = prepare(dsm, X0, Y0, 1.6, 100.0, dtm=dtm)
    assert prep.ground_z == pytest.approx(GROUND) and prep.ground_source == "dtm"


def test_coarse_dem_turns_occlusion_off_by_default():
    dem = make_dem(res=10.0, buildings=[(-10, 50, 10, 70, 10.0)])
    prep = prepare(dem, X0, Y0, 1.6, 300.0)
    assert not prep.occlusion
    assert footprint(prep, CAM, 0, 0).visible_pct == pytest.approx(100.0)


def test_camera_outside_dem_is_a_clear_error():
    with pytest.raises(ValueError, match="outside"):
        prepare(make_dem(size_m=200), X0 + 5000, Y0, 1.6, 100.0)


def test_nodata_cells_are_never_in_footprint():
    dem = make_dem(nodata=-9999.0)
    dem.band.WriteArray(np.full((100, 1200), -9999.0, dtype=np.float32), 0, 0)  # top 100 m strip
    fp = footprint(prepare(dem, X0, Y0, 1.6, 600.0), CAM, heading=0, pitch=0)
    top = Y0 + 600 - 100
    assert ogr.CreateGeometryFromWkt(fp.raw_wkt).GetEnvelope()[3] <= top + 1e-6


def test_360_photo_sees_the_whole_disc():
    cam = EquirectCamera(8000, 4000)
    fp = footprint(prepare(make_dem(), X0, Y0, 1.6, 200.0), cam, heading=0, pitch=0)
    assert fp.raw_area_m2 == pytest.approx(math.pi * 200.0 ** 2, rel=0.01)


def test_heading_change_is_fast_enough_for_live_slider():
    prep = prepare(make_dem(buildings=[(-10, 50, 10, 70, 10.0)]), X0, Y0, 1.6, 300.0)
    times = [timed_footprint(prep, CAM, h, 0)[1] for h in range(0, 360, 30)]
    print(f"footprint update: median {sorted(times)[len(times) // 2]:.1f} ms")
    assert sorted(times)[len(times) // 2] < 150
