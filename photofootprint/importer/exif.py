"""Pure-Python EXIF/XMP reader for JPEG and HEIC photos.

No QGIS imports and no third-party dependencies, so it runs on every QGIS
build and can be unit-tested on its own. It reads only the tags the plugin
needs and never raises on bad data inside a tag: values it can't decode come
back as None. Files it can't parse at all raise MetadataError.
"""

import re
import struct
from dataclasses import dataclass, field
from typing import List, Optional


class MetadataError(Exception):
    """The file is not a readable JPEG/HEIC or its EXIF block is corrupt."""


@dataclass
class PhotoMetadata:
    width: Optional[int] = None          # as displayed (after EXIF orientation)
    height: Optional[int] = None
    orientation: int = 1
    make: Optional[str] = None
    model: Optional[str] = None
    datetime: Optional[str] = None       # ISO 8601, local time unless offset known
    lat: Optional[float] = None
    lon: Optional[float] = None
    gps_alt: Optional[float] = None
    gps_error_m: Optional[float] = None  # GPSHPositioningError, if recorded
    heading: Optional[float] = None      # degrees clockwise from north
    heading_ref: Optional[str] = None    # "T" true or "M" magnetic
    pitch: Optional[float] = None        # degrees, positive up (from XMP)
    roll: Optional[float] = None
    focal_mm: Optional[float] = None
    focal_35mm: Optional[float] = None
    focal_plane_x_res: Optional[float] = None   # pixels per unit on the sensor
    focal_plane_unit_mm: Optional[float] = None
    projection: str = "perspective"      # or "360"
    warnings: List[str] = field(default_factory=list)


# ---------------------------------------------------------------- entry point

def read_metadata(path: str) -> PhotoMetadata:
    with open(path, "rb") as f:
        head = f.read(12)
        f.seek(0)
        if head[:2] == b"\xff\xd8":
            tiff, xmp, sof = _scan_jpeg(f)
        elif head[4:8] == b"ftyp":
            tiff, xmp, sof = _scan_heif(f)
        else:
            raise MetadataError("not a JPEG or HEIC file")

    md = PhotoMetadata()
    if tiff is not None:
        _apply_tiff(md, tiff)
    else:
        md.warnings.append("no EXIF block")
    if xmp:
        _apply_xmp(md, xmp)

    w, h = sof if sof else (md.width, md.height)
    if w and h:
        if md.orientation in (5, 6, 7, 8):
            w, h = h, w
        md.width, md.height = int(w), int(h)

    if md.projection == "perspective" and md.width and md.height:
        if md.width == 2 * md.height and md.focal_35mm is None and md.focal_mm is None:
            md.projection = "360"
            md.warnings.append("treated as 360 from 2:1 aspect ratio")
    return md


# ---------------------------------------------------------------- JPEG

_XMP_NS = b"http://ns.adobe.com/xap/1.0/\x00"
_SOF_MARKERS = {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}


def _scan_jpeg(f):
    data = f.read()
    tiff = xmp = sof = None
    pos = 2
    n = len(data)
    while pos + 4 <= n:
        if data[pos] != 0xFF:
            raise MetadataError(f"bad JPEG marker at byte {pos}")
        marker = data[pos + 1]
        if marker == 0xFF:          # fill byte
            pos += 1
            continue
        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
            pos += 2
            continue
        if marker in (0xD9, 0xDA):  # end of image / start of scan
            break
        (length,) = struct.unpack(">H", data[pos + 2:pos + 4])
        seg = data[pos + 4:pos + 2 + length]
        if marker == 0xE1:
            if seg.startswith(b"Exif\x00\x00") and tiff is None:
                tiff = seg[6:]
            elif seg.startswith(_XMP_NS) and xmp is None:
                xmp = seg[len(_XMP_NS):].decode("utf-8", "replace")
        elif marker in _SOF_MARKERS and len(seg) >= 5:
            h, w = struct.unpack(">HH", seg[1:5])
            sof = (w, h)
        pos += 2 + length
    return tiff, xmp, sof


# ---------------------------------------------------------------- HEIC / HEIF

def _boxes(data, start, end):
    pos = start
    while pos + 8 <= end:
        size, typ = struct.unpack(">I4s", data[pos:pos + 8])
        hdr = 8
        if size == 1:
            size = struct.unpack(">Q", data[pos + 8:pos + 16])[0]
            hdr = 16
        elif size == 0:
            size = end - pos
        if size < hdr or pos + size > end:
            raise MetadataError("corrupt HEIF box")
        yield typ.decode("latin-1"), pos + hdr, pos + size
        pos += size


def _scan_heif(f):
    data = f.read()
    meta = next(((s, e) for t, s, e in _boxes(data, 0, len(data)) if t == "meta"), None)
    if meta is None:
        raise MetadataError("HEIF file has no meta box")
    start, end = meta[0] + 4, meta[1]   # meta is a full box: skip version/flags

    items = {}       # item_id -> item type / content type
    locations = {}   # item_id -> list of (offset, length)
    for typ, s, e in _boxes(data, start, end):
        if typ == "iinf":
            version = data[s]
            n = 2 if version == 0 else 4
            for t2, s2, e2 in _boxes(data, s + 4 + n, e):
                if t2 != "infe" or data[s2] < 2:
                    continue
                v = data[s2]
                p = s2 + 4
                if v == 2:
                    item_id = struct.unpack(">H", data[p:p + 2])[0]
                    p += 2
                else:
                    item_id = struct.unpack(">I", data[p:p + 4])[0]
                    p += 4
                p += 2  # protection index
                item_type = data[p:p + 4].decode("latin-1")
                p += 4
                content_type = None
                if item_type == "mime":
                    rest = data[p:e2].split(b"\x00")
                    if len(rest) > 1:
                        content_type = rest[1].decode("latin-1")
                items[item_id] = (item_type, content_type)
        elif typ == "iloc":
            locations = _parse_iloc(data, s, e)

    tiff = xmp = None
    for item_id, (item_type, content_type) in items.items():
        if item_id not in locations:
            continue
        blob = b"".join(data[o:o + n] for o, n in locations[item_id])
        if item_type == "Exif" and tiff is None and len(blob) >= 4:
            skip = struct.unpack(">I", blob[:4])[0]
            tiff = blob[4 + skip:]
        elif item_type == "mime" and content_type == "application/rdf+xml" and xmp is None:
            xmp = blob.decode("utf-8", "replace")
    return tiff, xmp, None   # size comes from EXIF PixelX/YDimension


def _parse_iloc(data, s, e):
    version = data[s]
    p = s + 4
    b1, b2 = data[p], data[p + 1]
    off_size, len_size, base_size = b1 >> 4, b1 & 0xF, b2 >> 4
    idx_size = b2 & 0xF if version in (1, 2) else 0
    p += 2

    def read(size):
        nonlocal p
        if size == 0:
            return 0
        v = int.from_bytes(data[p:p + size], "big")
        p += size
        return v

    count = read(2 if version < 2 else 4)
    out = {}
    for _ in range(count):
        item_id = read(2 if version < 2 else 4)
        method = 0
        if version in (1, 2):
            method = read(2) & 0xF
        read(2)  # data reference index
        base = read(base_size)
        extents = []
        for _ in range(read(2)):
            if idx_size:
                read(idx_size)
            off = read(off_size)
            length = read(len_size)
            extents.append((base + off, length))
        if method == 0:   # file offsets only; idat-based items are not needed here
            out[item_id] = extents
        if p > e:
            raise MetadataError("corrupt iloc box")
    return out


# ---------------------------------------------------------------- TIFF / EXIF

_TYPE_SIZES = {1: 1, 2: 1, 3: 2, 4: 4, 5: 8, 7: 1, 9: 4, 10: 8}


class _Tiff:
    def __init__(self, buf):
        if len(buf) < 8:
            raise MetadataError("EXIF block too short")
        order = buf[:2]
        if order == b"II":
            self.e = "<"
        elif order == b"MM":
            self.e = ">"
        else:
            raise MetadataError("bad EXIF byte order")
        if struct.unpack(self.e + "H", buf[2:4])[0] != 42:
            raise MetadataError("bad EXIF header")
        self.buf = buf
        self.ifd0 = struct.unpack(self.e + "I", buf[4:8])[0]

    def ifd(self, offset):
        """Return {tag: value} for one IFD; undecodable entries are skipped."""
        buf, e = self.buf, self.e
        if offset is None or offset + 2 > len(buf):
            return {}
        (count,) = struct.unpack(e + "H", buf[offset:offset + 2])
        out = {}
        for i in range(min(count, 1000)):
            p = offset + 2 + 12 * i
            if p + 12 > len(buf):
                break
            tag, typ, n = struct.unpack(e + "HHI", buf[p:p + 8])
            size = _TYPE_SIZES.get(typ)
            if size is None:
                continue
            total = size * n
            if total <= 4:
                raw = buf[p + 8:p + 8 + total]
            else:
                (vo,) = struct.unpack(e + "I", buf[p + 8:p + 12])
                if vo + total > len(buf):
                    continue
                raw = buf[vo:vo + total]
            out[tag] = self._decode(typ, n, raw)
        return out

    def _decode(self, typ, n, raw):
        e = self.e
        if typ == 2:
            return raw.split(b"\x00")[0].decode("utf-8", "replace").strip()
        if typ in (1, 7):
            return list(raw)
        if typ == 3:
            return list(struct.unpack(e + "%dH" % n, raw))
        if typ == 4:
            return list(struct.unpack(e + "%dI" % n, raw))
        if typ == 9:
            return list(struct.unpack(e + "%di" % n, raw))
        if typ in (5, 10):
            fmt = "I" if typ == 5 else "i"
            vals = struct.unpack(e + "%d%s" % (2 * n, fmt), raw)
            return [None if vals[i + 1] == 0 else vals[i] / vals[i + 1]
                    for i in range(0, len(vals), 2)]
        return None


def _first(v):
    if isinstance(v, list):
        return v[0] if v else None
    return v


def _num(v):
    v = _first(v)
    return float(v) if isinstance(v, (int, float)) else None


def _dms(v, ref):
    if not isinstance(v, list) or len(v) != 3 or any(x is None for x in v):
        return None
    deg = v[0] + v[1] / 60.0 + v[2] / 3600.0
    return -deg if ref in ("S", "W") else deg


def _apply_tiff(md, buf):
    t = _Tiff(buf)
    ifd0 = t.ifd(t.ifd0)
    exif = t.ifd(_first(ifd0.get(0x8769)))
    gps = t.ifd(_first(ifd0.get(0x8825)))

    md.make = ifd0.get(0x010F) or None
    md.model = ifd0.get(0x0110) or None
    orient = _first(ifd0.get(0x0112))
    if orient in range(1, 9):
        md.orientation = orient

    dt = exif.get(0x9003) or ifd0.get(0x0132)
    if isinstance(dt, str) and re.match(r"\d{4}:\d\d:\d\d \d\d:\d\d:\d\d", dt):
        md.datetime = dt[:10].replace(":", "-") + "T" + dt[11:19]
        offset = exif.get(0x9011)
        if isinstance(offset, str) and re.match(r"[+-]\d\d:\d\d$", offset):
            md.datetime += offset

    md.focal_mm = _num(exif.get(0x920A))
    f35 = _num(exif.get(0xA405))
    md.focal_35mm = f35 if f35 else None   # 0 means unknown
    md.width = _first(exif.get(0xA002))
    md.height = _first(exif.get(0xA003))
    md.focal_plane_x_res = _num(exif.get(0xA20E))
    unit = _first(exif.get(0xA210))
    md.focal_plane_unit_mm = {2: 25.4, 3: 10.0, 4: 1.0, 5: 0.001}.get(unit)

    md.lat = _dms(gps.get(2), gps.get(1))
    md.lon = _dms(gps.get(4), gps.get(3))
    if md.lat is not None and not -90 <= md.lat <= 90:
        md.lat = None
        md.warnings.append("GPS latitude out of range")
    if md.lon is not None and not -180 <= md.lon <= 180:
        md.lon = None
        md.warnings.append("GPS longitude out of range")
    alt = _num(gps.get(6))
    if alt is not None:
        md.gps_alt = -alt if _first(gps.get(5)) == 1 else alt
    md.gps_error_m = _num(gps.get(0x1F))
    heading = _num(gps.get(0x11))
    if heading is not None:
        md.heading = heading % 360.0
        ref = gps.get(0x10)
        md.heading_ref = ref if ref in ("T", "M") else None


# ---------------------------------------------------------------- XMP

def xmp_value(xmp: str, name: str) -> Optional[str]:
    """Value of an XMP property written as an attribute or as an element."""
    q = re.escape(name)
    m = re.search(q + r'\s*=\s*"([^"]*)"', xmp) or re.search(q + r"\s*=\s*'([^']*)'", xmp)
    if not m:
        m = re.search("<" + q + r">([^<]*)</" + q + ">", xmp)
    return m.group(1).strip() if m else None


def _xmp_float(xmp, name):
    v = xmp_value(xmp, name)
    try:
        return float(v) if v is not None else None
    except ValueError:
        return None


def _apply_xmp(md, xmp):
    proj = xmp_value(xmp, "GPano:ProjectionType")
    if proj and proj.lower() == "equirectangular":
        md.projection = "360"
        heading = _xmp_float(xmp, "GPano:PoseHeadingDegrees")
        if heading is not None:
            md.heading = heading % 360.0
            md.heading_ref = md.heading_ref or "T"
        md.pitch = _xmp_float(xmp, "GPano:PosePitchDegrees")
        md.roll = _xmp_float(xmp, "GPano:PoseRollDegrees")
        w = _xmp_float(xmp, "GPano:FullPanoWidthPixels")
        h = _xmp_float(xmp, "GPano:FullPanoHeightPixels")
        if w and h and not md.width:
            md.width, md.height = int(w), int(h)
