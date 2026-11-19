"""Runs the Import photos algorithm inside a headless QGIS. Skipped without QGIS."""

import os

import pytest

qgis_core = pytest.importorskip("qgis.core")

from builders import make_jpeg, standard_tiff  # noqa: E402


@pytest.fixture
def photo_folder(tmp_path):
    d = tmp_path / "photos"
    d.mkdir()
    make_jpeg(d / "a.jpg", tiff=standard_tiff(lat=51.5007, lon=-0.1246, heading=90))
    make_jpeg(d / "b.jpg", tiff=standard_tiff(lat=51.5010, lon=-0.1240, heading=None))
    make_jpeg(d / "c.jpg", tiff=standard_tiff(lat=None))
    (d / "d.jpg").write_bytes(b"not a photo")
    return d


def test_import_writes_geopackage(qgis_app, photo_folder, tmp_path):
    import processing
    from qgis.core import QgsVectorLayer

    out = str(tmp_path / "project" / "photos.gpkg")
    os.makedirs(os.path.dirname(out))
    result = processing.run("photofootprint:importphotos", {
        "FOLDER": str(photo_folder), "RECURSIVE": False, "CRS": "EPSG:27700",
        "EYE_HEIGHT": 1.6, "DEFAULT_PITCH": -5.0, "DEFAULT_FOCAL_35MM": 26.0,
        "OUTPUT": out,
    })
    assert result["IMPORTED"] == 2
    assert result["SKIPPED"] == 2

    layer = QgsVectorLayer(out, "photos", "ogr")
    assert layer.isValid()
    assert layer.crs().authid() == "EPSG:27700"
    feats = {f["path"]: f for f in layer.getFeatures()}
    assert set(feats) == {os.path.join("..", "photos", "a.jpg"), os.path.join("..", "photos", "b.jpg")}

    a = feats[os.path.join("..", "photos", "a.jpg")]
    # Big Ben is at about E 530270, N 179640 on the British National Grid
    assert abs(a["x"] - 530270) < 50 and abs(a["y"] - 179640) < 50
    assert a.geometry().asPoint().x() == pytest.approx(a["x"])
    assert a["heading"] == pytest.approx(90.0)
    assert a["pitch"] == pytest.approx(-5.0) and a["pitch_source"] == "default"
    assert a["fov_source"] == "35mm" and 66 < a["hfov"] < 68.5
    assert a["status"] == "ok"

    b = feats[os.path.join("..", "photos", "b.jpg")]
    assert b["status"] == "no_heading"
    assert b["heading"] is None or str(b["heading"]) == "NULL"


def test_auto_crs_is_utm_when_none_given(qgis_app, photo_folder, tmp_path):
    import processing
    from qgis.core import QgsVectorLayer
    out = str(tmp_path / "auto.gpkg")
    processing.run("photofootprint:importphotos", {"FOLDER": str(photo_folder), "OUTPUT": out})
    layer = QgsVectorLayer(out, "photos", "ogr")
    assert layer.crs().authid() == "EPSG:32630"   # London is in UTM zone 30N


def test_empty_folder_is_a_clear_error(qgis_app, tmp_path):
    import processing
    from qgis.core import QgsProcessingException
    with pytest.raises(QgsProcessingException, match="No JPEG or HEIC"):
        processing.run("photofootprint:importphotos",
                       {"FOLDER": str(tmp_path), "OUTPUT": "TEMPORARY_OUTPUT"})
