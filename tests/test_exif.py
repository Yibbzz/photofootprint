import os

import pytest

from photofootprint.camera import intrinsics
from photofootprint.importer.exif import MetadataError, read_metadata
from photofootprint.importer.scan import build_record, photo_id, scan

from builders import (ASCII, RATIONAL, build_tiff, make_heic, make_jpeg,
                      standard_tiff)


def test_reads_standard_phone_jpeg(tmp_path):
    p = make_jpeg(tmp_path / "a.jpg", tiff=standard_tiff(lat=51.5, lon=-0.12, heading=45.0))
    md = read_metadata(str(p))
    assert md.lat == pytest.approx(51.5, abs=1e-6)
    assert md.lon == pytest.approx(-0.12, abs=1e-6)
    assert md.heading == pytest.approx(45.0)
    assert md.heading_ref == "T"
    assert md.gps_alt == pytest.approx(12.5)
    assert md.focal_mm == pytest.approx(4.25)
    assert md.focal_35mm == 26
    assert (md.width, md.height) == (4032, 3024)
    assert (md.make, md.model) == ("Apple", "iPhone 13")
    assert md.datetime == "2026-11-14T10:30:05+00:00"
    assert md.projection == "perspective"


def test_big_endian_and_southern_western_hemisphere(tmp_path):
    p = make_jpeg(tmp_path / "b.jpg", tiff=standard_tiff(lat=-33.86, lon=-70.65, alt=-3.0, order=">"))
    md = read_metadata(str(p))
    assert md.lat == pytest.approx(-33.86, abs=1e-6)
    assert md.lon == pytest.approx(-70.65, abs=1e-6)
    assert md.gps_alt == pytest.approx(-3.0)


def test_portrait_orientation_swaps_size(tmp_path):
    p = make_jpeg(tmp_path / "c.jpg", tiff=standard_tiff(orientation=6))
    md = read_metadata(str(p))
    assert (md.width, md.height) == (3024, 4032)


def test_gpano_xmp_marks_360_and_sets_heading(tmp_path):
    xmp = ('<x:xmpmeta><rdf:Description GPano:ProjectionType="equirectangular" '
           'GPano:PoseHeadingDegrees="370.0"><GPano:PosePitchDegrees>2.5</GPano:PosePitchDegrees>'
           '</rdf:Description></x:xmpmeta>')
    p = make_jpeg(tmp_path / "d.jpg", 8000, 4000, tiff=standard_tiff(heading=None, f35=None, focal=None), xmp=xmp)
    md = read_metadata(str(p))
    assert md.projection == "360"
    assert md.heading == pytest.approx(10.0)
    assert md.pitch == pytest.approx(2.5)


def test_two_to_one_without_focal_is_treated_as_360(tmp_path):
    p = make_jpeg(tmp_path / "e.jpg", 6000, 3000, tiff=standard_tiff(f35=None, focal=None, width=6000, height=3000))
    md = read_metadata(str(p))
    assert md.projection == "360"
    assert any("2:1" in w for w in md.warnings)


def test_heic_exif(tmp_path):
    p = make_heic(tmp_path / "f.heic", standard_tiff(lat=55.95, lon=-3.19, heading=300.0))
    md = read_metadata(str(p))
    assert md.lat == pytest.approx(55.95, abs=1e-6)
    assert md.heading == pytest.approx(300.0)
    assert (md.width, md.height) == (4032, 3024)


def test_zero_denominator_gives_none_not_crash(tmp_path):
    tiff = build_tiff([], [], [(1, ASCII, "N"), (2, RATIONAL, [(51, 0), (0, 1), (0, 1)]),
                               (3, ASCII, "E"), (4, RATIONAL, [(0, 1), (0, 1), (0, 1)])])
    md = read_metadata(str(make_jpeg(tmp_path / "g.jpg", tiff=tiff)))
    assert md.lat is None


def test_not_an_image_raises(tmp_path):
    p = tmp_path / "h.jpg"
    p.write_bytes(b"hello world, not a jpeg")
    with pytest.raises(MetadataError):
        read_metadata(str(p))


def test_truncated_exif_is_survivable(tmp_path):
    tiff = standard_tiff()[:40]
    md = read_metadata(str(make_jpeg(tmp_path / "i.jpg", tiff=tiff)))
    assert md.lat is None   # pointers into the missing part are ignored


# ------------------------------------------------------------ intrinsics


class _MD:
    def __init__(self, **kw):
        self.width, self.height = 4032, 3024
        self.make = self.model = None
        self.focal_mm = self.focal_35mm = None
        self.focal_plane_x_res = self.focal_plane_unit_mm = None
        self.projection = "perspective"
        self.__dict__.update(kw)


def test_fov_from_35mm_equivalent():
    i = intrinsics.resolve(_MD(focal_35mm=26))
    assert i.fov_source == "35mm"
    assert 66 < i.hfov < 68.5


def test_fov_from_sensor_database():
    i = intrinsics.resolve(_MD(make="SONY", model="ILCE-7M3", focal_mm=24.0, width=6000, height=4000))
    assert i.fov_source == "focal+sensor"
    assert i.sensor_w_mm == 35.6
    assert i.focal_px == pytest.approx(24 * 6000 / 35.6)


def test_fov_from_focal_plane_resolution():
    # 4000 px across a 20 mm sensor: 200 px/mm = 5080 px/inch
    i = intrinsics.resolve(_MD(focal_mm=10.0, width=4000, height=3000,
                               focal_plane_x_res=5080, focal_plane_unit_mm=25.4))
    assert i.fov_source == "focal_plane_res"
    assert i.sensor_w_mm == pytest.approx(20.0)


def test_fov_default_is_flagged():
    assert intrinsics.resolve(_MD()).fov_source == "default"


# ------------------------------------------------------------ scanning


def test_scan_keeps_good_photos_and_explains_skipped(tmp_path):
    make_jpeg(tmp_path / "good.jpg", tiff=standard_tiff())
    make_jpeg(tmp_path / "nogps.jpg", tiff=standard_tiff(lat=None))
    make_jpeg(tmp_path / "nohead.jpg", tiff=standard_tiff(heading=None))
    (tmp_path / "broken.jpg").write_bytes(b"\xff\xd8\x00garbage")
    (tmp_path / "notes.txt").write_text("ignored")
    sub = tmp_path / "day2"
    sub.mkdir()
    make_jpeg(sub / "deep.JPG", tiff=standard_tiff(lat=52.0))

    records, skipped = scan(str(tmp_path))
    names = sorted(os.path.basename(r.path) for r in records)
    assert names == ["good.jpg", "nohead.jpg"]
    reasons = dict((os.path.basename(p), r) for p, r in skipped)
    assert reasons["nogps.jpg"] == "no GPS position"
    assert "broken.jpg" in reasons

    nohead = next(r for r in records if r.path.endswith("nohead.jpg"))
    assert nohead.status == "no_heading" and nohead.heading is None
    assert nohead.pitch_source == "default"

    records, _ = scan(str(tmp_path), recursive=True)
    assert len(records) == 3




def test_photo_id_survives_rename(tmp_path):
    p = make_jpeg(tmp_path / "x.jpg", tiff=standard_tiff())
    before = photo_id(str(p))
    p2 = tmp_path / "renamed.jpg"
    os.rename(p, p2)
    assert photo_id(str(p2)) == before


def test_missing_focal_length_note(tmp_path):
    p = make_jpeg(tmp_path / "nf.jpg", tiff=standard_tiff(f35=None, focal=None))
    r = build_record(str(p))
    assert r.fov_source == "default"
    assert any("assumed 26" in n for n in r.notes)
