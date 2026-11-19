"""Generate footprints algorithm and the live footprint panel, in a headless QGIS."""

import os

import numpy as np
import pytest

pytest.importorskip("qgis.core")

from builders import make_jpeg, standard_tiff  # noqa: E402


def write_dem(path, x, y, size=800, res=1.0, building=None):
    """GeoTIFF in EPSG:27700 centred on (x, y). building = (dx0, dy0, dx1, dy1, h)."""
    from osgeo import gdal, osr
    n = int(size / res)
    z = np.full((n, n), 20.0, dtype=np.float32)
    left, top = x - size / 2, y + size / 2
    if building:
        dx0, dy0, dx1, dy1, h = building
        z[int((top - (y + dy1)) / res):int((top - (y + dy0)) / res),
          int((x + dx0 - left) / res):int((x + dx1 - left) / res)] += h
    ds = gdal.GetDriverByName("GTiff").Create(path, n, n, 1, gdal.GDT_Float32)
    ds.SetGeoTransform((left, res, 0, top, 0, -res))
    srs = osr.SpatialReference()
    srs.ImportFromEPSG(27700)
    ds.SetProjection(srs.ExportToWkt())
    ds.GetRasterBand(1).WriteArray(z)
    ds = None
    return path


@pytest.fixture
def site(qgis_app, tmp_path):
    """Imported photo layer (one facing north, one with no heading) and a DSM with a building."""
    import processing
    from qgis.core import QgsRasterLayer, QgsVectorLayer
    photos = tmp_path / "photos"
    photos.mkdir()
    make_jpeg(photos / "north.jpg", tiff=standard_tiff(lat=51.5007, lon=-0.1246, heading=0))
    make_jpeg(photos / "nohead.jpg", tiff=standard_tiff(lat=51.5008, lon=-0.1245, heading=None))
    gpkg = str(tmp_path / "photos.gpkg")
    processing.run("photofootprint:importphotos",
                   {"FOLDER": str(photos), "CRS": "EPSG:27700", "OUTPUT": gpkg})
    layer = QgsVectorLayer(gpkg, "photos", "ogr")
    north = next(f for f in layer.getFeatures() if f["status"] == "ok")
    x, y = north["x"], north["y"]
    dem_path = write_dem(str(tmp_path / "dsm.tif"), x, y, building=(-15, 40, 15, 60, 12.0))
    dem = QgsRasterLayer(dem_path, "dsm")
    assert dem.isValid()
    return layer, dem, (x, y), tmp_path


def test_generate_footprints(site):
    import processing
    from qgis.core import QgsGeometry, QgsPointXY, QgsVectorLayer
    layer, dem, (x, y), tmp = site
    vis_path, raw_path = str(tmp / "vis.gpkg"), str(tmp / "raw.gpkg")
    res = processing.run("photofootprint:generatefootprints", {
        "PHOTOS": layer, "DEM": dem, "MAX_RANGE": 300, "OCCLUSION": 0,
        "VISIBLE": vis_path, "RAW": raw_path,
    })
    assert res["DONE"] == 1 and res["SKIPPED"] == 1   # the no-heading photo is skipped

    vis = next(QgsVectorLayer(vis_path, "v", "ogr").getFeatures())
    raw = next(QgsVectorLayer(raw_path, "r", "ogr").getFeatures())
    assert vis["occlusion"] == 1 and vis["dem_res_m"] == 1.0
    assert vis["ground_source"] == "dsm_lowest_2m"
    assert vis["ground_z"] == pytest.approx(20.0)
    assert vis["visible_pct"] < 95

    behind = QgsGeometry.fromPointXY(QgsPointXY(x, y + 150))
    assert raw.geometry().contains(behind)
    assert not vis.geometry().contains(behind)
    assert vis.geometry().area() == pytest.approx(vis["area_m2"], rel=1e-3)


class _FakeIface:
    def __init__(self, canvas):
        self._canvas = canvas

    def mapCanvas(self):
        return self._canvas


def test_live_panel_follows_slider_and_saves(site):
    from qgis.core import QgsProject
    from qgis.gui import QgsMapCanvas
    from photofootprint.ui.footprint_dock import FootprintDock
    layer, dem, (x, y), _ = site
    QgsProject.instance().addMapLayers([layer, dem])
    try:
        canvas = QgsMapCanvas()
        canvas.setDestinationCrs(layer.crs())
        dock = FootprintDock(_FakeIface(canvas))
        dock.photo_combo.setLayer(layer)
        dock.dem_combo.setLayer(dem)
        dock.range_spin.setValue(200)

        north = next(f for f in layer.getFeatures() if f["status"] == "ok")
        layer.selectByIds([north.id()])
        dock.update_footprint()
        fp_north = dock.last_footprint
        assert fp_north is not None and fp_north.visible_area_m2 > 0
        assert not dock.visible_band.asGeometry().isEmpty()
        assert dock.visible_band.asGeometry().centroid().asPoint().y() > y

        dock.heading.spin.setValue(90.0)          # turn east with the spin box
        dock.update_footprint()
        east = dock.visible_band.asGeometry().centroid().asPoint()
        assert east.x() - x > 30
        assert dock.last_footprint.visible_pct == pytest.approx(100.0)   # nothing east

        dock.pitch.slider.setValue(-150)          # slider is in tenths of a degree
        assert dock.pitch.value() == pytest.approx(-15.0)

        dock.save()
        saved = layer.getFeature(north.id())
        assert saved["heading"] == pytest.approx(90.0)
        assert saved["heading_source"] == "manual"
        assert saved["pitch"] == pytest.approx(-15.0) and saved["pitch_source"] == "manual"
        dock.cleanup()
    finally:
        QgsProject.instance().removeAllMapLayers()


def test_panel_handles_photo_without_heading(site):
    from qgis.core import QgsProject
    from qgis.gui import QgsMapCanvas
    from photofootprint.ui.footprint_dock import FootprintDock
    layer, dem, _, _ = site
    QgsProject.instance().addMapLayers([layer, dem])
    try:
        dock = FootprintDock(_FakeIface(QgsMapCanvas()))
        dock.photo_combo.setLayer(layer)
        dock.dem_combo.setLayer(dem)
        nohead = next(f for f in layer.getFeatures() if f["status"] == "no_heading")
        layer.selectByIds([nohead.id()])
        assert "no heading" in dock.photo_label.text()
        dock.update_footprint()   # shows a footprint at 0 degrees so the user can drag it
        assert dock.last_footprint is not None
        dock.cleanup()
    finally:
        QgsProject.instance().removeAllMapLayers()
