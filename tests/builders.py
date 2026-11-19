"""Build tiny JPEG/HEIC files with exact EXIF/XMP content for tests.

The JPEGs have headers only (no image data), which is all the metadata
reader looks at.
"""

import struct

BYTE, ASCII, SHORT, LONG, RATIONAL, SRATIONAL = 1, 2, 3, 4, 5, 10

EXIF_PTR, GPS_PTR = 0x8769, 0x8825


def _encode(e, typ, value):
    if typ == ASCII:
        raw = value.encode() + b"\x00"
        return raw, len(raw)
    if typ in (BYTE, SHORT, LONG):
        vals = value if isinstance(value, (list, tuple)) else [value]
        fmt = {BYTE: "B", SHORT: "H", LONG: "I"}[typ]
        return struct.pack(e + "%d%s" % (len(vals), fmt), *vals), len(vals)
    pairs = value if isinstance(value[0], (list, tuple)) else [value]
    flat = [x for p in pairs for x in p]
    return struct.pack(e + "%d%s" % (len(flat), "I" if typ == RATIONAL else "i"), *flat), len(pairs)


def _ifd(e, entries, offset):
    """entries: list of (tag, type, value). Returns bytes placed at `offset`."""
    entries = sorted(entries)
    n = len(entries)
    data_start = offset + 2 + 12 * n + 4
    head = struct.pack(e + "H", n)
    data = b""
    for tag, typ, value in entries:
        raw, count = _encode(e, typ, value)
        if len(raw) <= 4:
            head += struct.pack(e + "HHI", tag, typ, count) + raw.ljust(4, b"\x00")
        else:
            head += struct.pack(e + "HHII", tag, typ, count, data_start + len(data))
            data += raw + (b"\x00" if len(raw) % 2 else b"")
    return head + struct.pack(e + "I", 0) + data


def build_tiff(ifd0, exif=None, gps=None, order="<"):
    e = order
    ifd0 = list(ifd0)
    if exif is not None:
        ifd0.append((EXIF_PTR, LONG, 0))
    if gps is not None:
        ifd0.append((GPS_PTR, LONG, 0))
    # Pass 1: sizes (pointer values don't change sizes)
    size0 = len(_ifd(e, ifd0, 8))
    exif_off = 8 + size0
    size_exif = len(_ifd(e, exif, exif_off)) if exif is not None else 0
    gps_off = exif_off + size_exif
    # Pass 2: real pointers
    ifd0 = [(t, ty, exif_off if t == EXIF_PTR else gps_off if t == GPS_PTR else v)
            for t, ty, v in ifd0]
    out = (b"II" if e == "<" else b"MM") + struct.pack(e + "HI", 42, 8)
    out += _ifd(e, ifd0, 8)
    if exif is not None:
        out += _ifd(e, exif, exif_off)
    if gps is not None:
        out += _ifd(e, gps, gps_off)
    return out


def dms(deg):
    deg = abs(deg)
    d = int(deg)
    m = int((deg - d) * 60)
    s = round(((deg - d) * 60 - m) * 60 * 10000)
    return [(d, 1), (m, 1), (s, 10000)]


def standard_tiff(lat=51.5, lon=-0.12, heading=45.0, f35=26, focal=(425, 100),
                  orientation=1, width=4032, height=3024, order="<", alt=12.5,
                  make="Apple", model="iPhone 13"):
    ifd0 = [(0x010F, ASCII, make), (0x0110, ASCII, model), (0x0112, SHORT, orientation)]
    exif = [(0x9003, ASCII, "2026:11:14 10:30:05"), (0x9011, ASCII, "+00:00"),
            (0xA002, LONG, width), (0xA003, LONG, height)]
    if focal:
        exif.append((0x920A, RATIONAL, focal))
    if f35:
        exif.append((0xA405, SHORT, f35))
    gps = []
    if lat is not None:
        gps += [(1, ASCII, "N" if lat >= 0 else "S"), (2, RATIONAL, dms(lat)),
                (3, ASCII, "E" if lon >= 0 else "W"), (4, RATIONAL, dms(lon))]
    if alt is not None:
        gps += [(5, BYTE, 1 if alt < 0 else 0), (6, RATIONAL, (int(round(abs(alt) * 10)), 10))]
    if heading is not None:
        gps += [(0x10, ASCII, "T"), (0x11, RATIONAL, (int(heading * 100), 100))]
    return build_tiff(ifd0, exif, gps, order)


def make_jpeg(path, width=4032, height=3024, tiff=None, xmp=None):
    out = b"\xff\xd8"
    if tiff is not None:
        seg = b"Exif\x00\x00" + tiff
        out += b"\xff\xe1" + struct.pack(">H", len(seg) + 2) + seg
    if xmp is not None:
        seg = b"http://ns.adobe.com/xap/1.0/\x00" + xmp.encode()
        out += b"\xff\xe1" + struct.pack(">H", len(seg) + 2) + seg
    sof = struct.pack(">BHHB", 8, height, width, 3) + b"\x01\x22\x00\x02\x11\x01\x03\x11\x01"
    out += b"\xff\xc0" + struct.pack(">H", len(sof) + 2) + sof
    out += b"\xff\xda\x00\x08\x01\x01\x00\x00\x3f\x00" + b"\xff\xd9"
    with open(path, "wb") as f:
        f.write(out)
    return path


def _box(typ, payload):
    return struct.pack(">I4s", 8 + len(payload), typ.encode()) + payload


def _full(typ, version, payload):
    return _box(typ, bytes([version, 0, 0, 0]) + payload)


def make_heic(path, tiff):
    ftyp = _box("ftyp", b"heic" + b"\x00\x00\x00\x00" + b"mif1heic")
    hdlr = _full("hdlr", 0, b"\x00" * 4 + b"pict" + b"\x00" * 12 + b"\x00")
    infe = _full("infe", 2, struct.pack(">HH", 1, 0) + b"Exif" + b"\x00")
    iinf = _full("iinf", 0, struct.pack(">H", 1) + infe)
    payload = struct.pack(">I", 0) + tiff

    def build(offset):
        iloc = _full("iloc", 0, bytes([0x44, 0x00]) + struct.pack(">HHHHII", 1, 1, 0, 1, offset, len(payload)))
        return _full("meta", 0, hdlr + iinf + iloc)

    meta = build(0)
    mdat_offset = len(ftyp) + len(meta) + 8
    out = ftyp + build(mdat_offset) + _box("mdat", payload)
    with open(path, "wb") as f:
        f.write(out)
    return path
