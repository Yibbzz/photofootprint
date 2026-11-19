"""Scan a folder of photos into records ready for the photo layer.

Pure Python: no QGIS imports. Photos that can't be used are returned in a
separate list with a reason, never raised.
"""

import hashlib
import os
from dataclasses import dataclass, field
from typing import Iterator, List, Optional, Tuple

from ..camera import intrinsics
from .exif import MetadataError, read_metadata

PHOTO_EXTENSIONS = {".jpg", ".jpeg", ".heic", ".heif"}


@dataclass
class PhotoRecord:
    photo_id: str
    path: str                     # absolute
    type: str                     # "perspective" or "360"
    lat: float
    lon: float
    gps_alt: Optional[float]
    gps_error_m: Optional[float]
    datetime: Optional[str]
    make: Optional[str]
    model: Optional[str]
    width: int
    height: int
    heading: Optional[float]
    heading_ref: Optional[str]
    heading_source: str           # "exif" or "none"
    pitch: float
    pitch_source: str             # "exif" or "default"
    roll: float
    height_agl: float             # camera height above ground, metres
    hfov: float
    vfov: float
    focal_px: Optional[float]
    focal_mm: Optional[float]
    focal_35mm: Optional[float]
    sensor_w_mm: Optional[float]
    fov_source: str
    status: str                   # "ok" or "no_heading"
    notes: List[str] = field(default_factory=list)


def iter_photos(folder: str, recursive: bool = False) -> Iterator[str]:
    if recursive:
        for root, dirs, files in os.walk(folder):
            dirs.sort()
            for name in sorted(files):
                if os.path.splitext(name)[1].lower() in PHOTO_EXTENSIONS:
                    yield os.path.join(root, name)
    else:
        for name in sorted(os.listdir(folder)):
            p = os.path.join(folder, name)
            if os.path.isfile(p) and os.path.splitext(name)[1].lower() in PHOTO_EXTENSIONS:
                yield p


def photo_id(path: str) -> str:
    """Stable ID from the file's content, so it survives renames and moves."""
    h = hashlib.sha1()
    h.update(str(os.path.getsize(path)).encode())
    with open(path, "rb") as f:
        h.update(f.read(65536))
    return h.hexdigest()[:12]


def build_record(path: str, eye_height: float = 1.6, default_pitch: float = 0.0,
                 default_focal_35mm: float = intrinsics.DEFAULT_FOCAL_35MM) -> PhotoRecord:
    """Raise ValueError (with a user-facing reason) if the photo can't be used."""
    try:
        md = read_metadata(path)
    except MetadataError as e:
        raise ValueError(str(e))
    if md.lat is None or md.lon is None:
        raise ValueError("no GPS position")
    if not md.width or not md.height:
        raise ValueError("image size unknown")
    intr = intrinsics.resolve(md, default_focal_35mm)

    notes = list(md.warnings)
    if intr.fov_source == "default":
        notes.append(f"no focal length: assumed {default_focal_35mm:g} mm equivalent")
    if md.heading_ref == "M":
        notes.append("heading is magnetic, not true north")
    if md.heading is None:
        notes.append("no compass heading")

    return PhotoRecord(
        photo_id=photo_id(path),
        path=os.path.abspath(path),
        type=md.projection,
        lat=md.lat, lon=md.lon,
        gps_alt=md.gps_alt, gps_error_m=md.gps_error_m,
        datetime=md.datetime, make=md.make, model=md.model,
        width=md.width, height=md.height,
        heading=md.heading, heading_ref=md.heading_ref,
        heading_source="exif" if md.heading is not None else "none",
        pitch=md.pitch if md.pitch is not None else default_pitch,
        pitch_source="exif" if md.pitch is not None else "default",
        roll=md.roll if md.roll is not None else 0.0,
        height_agl=eye_height,
        hfov=round(intr.hfov, 3), vfov=round(intr.vfov, 3),
        focal_px=round(intr.focal_px, 2) if intr.focal_px else None,
        focal_mm=md.focal_mm, focal_35mm=md.focal_35mm,
        sensor_w_mm=round(intr.sensor_w_mm, 3) if intr.sensor_w_mm else None,
        fov_source=intr.fov_source,
        status="ok" if md.heading is not None else "no_heading",
        notes=notes,
    )


def scan(folder: str, recursive: bool = False, **kwargs) -> Tuple[List[PhotoRecord], List[Tuple[str, str]]]:
    """Return (records, skipped) where skipped is a list of (path, reason)."""
    records, skipped = [], []
    for path in iter_photos(folder, recursive):
        try:
            records.append(build_record(path, **kwargs))
        except (ValueError, OSError) as e:
            skipped.append((path, str(e)))
        except Exception as e:   # never let one bad file stop the import
            skipped.append((path, f"unexpected error: {e!r}"))
    return records, skipped
