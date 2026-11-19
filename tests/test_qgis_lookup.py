"""M3: photo viewer, image click -> map, map click -> photos, Find photos algorithm."""

import math

import pytest

pytest.importorskip("qgis.core")

from builders import make_real_jpeg, standard_tiff  # noqa: E402
from test_qgis_footprints import _FakeIface, write_dem  # noqa: E402

M_PER_DEG_LAT = 111_320.0


@pytest.fixture
def scene(qgis_app, tmp_path):
    """Photo A looks north at a spot 30 m ahead, with a building 50-70 m ahead.
    Photo B looks north from 60 m further south. Photo C has no heading."""
    import processing
    from qgis.core import QgsProject, QgsRasterLayer, QgsVectorLayer
    lat, lon = 51.5007, -0.1246
    d = tmp_path / "photos"
    d.mkdir()
    make_real_jpeg(str(d / "a.jpg"), 400, 300, standard_tiff(lat=lat, lon=lon, heading=0))
    make_real_jpeg(str(d / "b.jpg"), 400, 300, standard_tiff(lat=lat - 60 / M_PER_DEG_LAT, lon=lon, heading=0))
    make_real_jpeg(str(d / "c.jpg"), 400, 300, standard_tiff(lat=lat + 5 / M_PER_DEG_LAT, lon=lon, heading=None))
    gpkg = str(tmp_path / "photos.gpkg")
    processing.run("photofootprint:importphotos",
                   {"FOLDER": str(d), "CRS": "EPSG:27700", "OUTPUT": gpkg})
    layer = QgsVectorLayer(gpkg, "photos", "ogr")
    feats = {f["path"].split("/")[-1]: f for f in layer.getFeatures()}
    a = feats["a.jpg"]
    x, y = a["x"], a["y"]
    dem = QgsRasterLayer(write_dem(str(tmp_path / "dsm.tif"), x, y, building=(-15, 50, 15, 70, 12.0)), "dsm")
    QgsProject.instance().addMapLayers([layer, dem])
    yield layer, dem, feats, (x, y)
    QgsProject.instance().removeAllMapLayers()


def test_photo_paths_are_stored_relative_and_resolve():
    from photofootprint.paths import resolve_photo_path
    assert resolve_photo_path("photos/a.jpg", "/data/site/photos.gpkg|layername=photos") == "/data/site/photos/a.jpg"
    assert resolve_photo_path("/abs/a.jpg", "/data/site/photos.gpkg") == "/abs/a.jpg"


def test_find_photos_algorithm(scene, tmp_path):
    import processing
    from qgis.core import QgsFeature, QgsGeometry, QgsPointXY, QgsVectorLayer
    layer, dem, feats, (x, y) = scene
    pts = QgsVectorLayer("Point?crs=EPSG:27700", "pts", "memory")
    for px, py in ((x, y + 30), (x, y + 120)):        # in front of / behind the building
        f = QgsFeature()
        f.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(px, py)))
        pts.dataProvider().addFeature(f)
    out = str(tmp_path / "found.gpkg")
    res = processing.run("photofootprint:findphotos", {
        "POINTS": pts, "PHOTOS": layer, "DEM": dem, "MAX_RANGE": 300, "TOP_N": 3, "OUTPUT": out})
    assert res["POINTS"] == 2 and res["FOUND"] == 1
    rows = sorted(QgsVectorLayer(out, "f", "ogr").getFeatures(), key=lambda f: f["rank"])
    assert [r["photo_id"] for r in rows] == [feats["a.jpg"]["photo_id"], feats["b.jpg"]["photo_id"]]
    assert rows[0]["distance_m"] == pytest.approx(30, abs=2)
    assert abs(rows[0]["u"] - 200) < 15      # straight ahead: near the middle column


def _panel(layer, dem, x, y):
    from qgis.gui import QgsMapCanvas
    from photofootprint.ui.footprint_dock import FootprintDock
    canvas = QgsMapCanvas()
    canvas.setDestinationCrs(layer.crs())
    dock = FootprintDock(_FakeIface(canvas))
    dock.photo_combo.setLayer(layer)
    dock.dem_combo.setLayer(dem)
    dock.range_spin.setValue(300)
    return dock, canvas


def test_map_click_finds_and_opens_best_photo_with_marker(scene):
    from qgis.core import QgsPointXY
    layer, dem, feats, (x, y) = scene
    dock, canvas = _panel(layer, dem, x, y)
    try:
        matches = dock.find_photos_at(QgsPointXY(x, y + 30))
        assert [m.key for m in matches] == [feats["a.jpg"].id(), feats["b.jpg"].id()]
        assert dock.results.count() == 2
        assert dock._photo[1] == feats["a.jpg"].id()          # best photo opened
        assert dock.image.has_image()
        u, v = dock.image.marker_pos()
        assert abs(u - matches[0].u) < 0.5 and abs(v - matches[0].v) < 0.5

        dock._open_result(dock.results.item(1))                 # choose the second photo
        assert dock._photo[1] == feats["b.jpg"].id()
        assert dock.image.marker_pos()[1] < v                   # further away: higher in the frame

        assert dock.find_photos_at(QgsPointXY(x, y + 120)) == []   # behind the building
        assert "No photo" in dock.results.item(0).text()
    finally:
        dock.cleanup()
        dock.deleteLater()
        canvas.deleteLater()


def test_image_click_lands_on_the_map_where_lookup_said(scene):
    from qgis.core import QgsPointXY
    layer, dem, feats, (x, y) = scene
    dock, canvas = _panel(layer, dem, x, y)
    try:
        m = dock.find_photos_at(QgsPointXY(x + 4, y + 30))[0]
        dock.image_clicked(m.u, m.v)                       # click where the crosshair is
        hx, hy, hz, dist = dock.last_ground_hit
        assert math.hypot(hx - (x + 4), hy - (y + 30)) < 0.5
        assert dist == pytest.approx(math.hypot(4, 30), abs=0.5)
        assert dock.hit_marker.isVisible()

        cam = dock.current_setup().camera
        dock.image_clicked(cam.cx, cam.cy - cam.focal_px * math.tan(math.radians(5)))
        assert 49 < dock.last_ground_hit[1] - y < 51          # 5 degrees up: hits the wall

        dock.image_clicked(cam.cx, 2)                           # 25 degrees up: clears the roof
        assert dock.last_ground_hit is None and not dock.hit_marker.isVisible()
    finally:
        dock.cleanup()
        dock.deleteLater()
        canvas.deleteLater()


def test_prev_next_walks_photos(scene):
    layer, dem, feats, (x, y) = scene
    dock, canvas = _panel(layer, dem, x, y)
    try:
        layer.selectByIds([feats["a.jpg"].id()])
        seen = {dock._photo[1]}
        for _ in range(3):
            dock.step_photo(1)
            seen.add(dock._photo[1] if dock._photo else layer.selectedFeatureIds()[0])
        assert seen == {f.id() for f in feats.values()}
    finally:
        dock.cleanup()
        dock.deleteLater()
        canvas.deleteLater()


def test_true_north_is_turned_into_grid_north(qgis_app):
    """A photo facing true north in London points about 1.5 degrees west of grid north on BNG."""
    from qgis.core import QgsCoordinateReferenceSystem, QgsCoordinateTransformContext, QgsPointXY
    from photofootprint.geo import true_north_offset
    off = true_north_offset(QgsPointXY(-0.1246, 51.5007), QgsCoordinateReferenceSystem("EPSG:4326"),
                            QgsCoordinateReferenceSystem("EPSG:27700"), QgsCoordinateTransformContext())
    assert -1.6 < off < -1.3
    assert true_north_offset(QgsPointXY(-2.0, 53.0), QgsCoordinateReferenceSystem("EPSG:4326"),
                             QgsCoordinateReferenceSystem("EPSG:27700"),
                             QgsCoordinateTransformContext()) == pytest.approx(0.0, abs=1e-3)
