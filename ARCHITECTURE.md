# Architecture

## Layout

| Path | QGIS needed? | Responsibility |
| --- | --- | --- |
| `photofootprint/camera/model.py` | No | Pinhole and equirectangular camera models: pixel → 3D ray, 3D point → pixel |
| `photofootprint/camera/intrinsics.py` | No | Field of view from EXIF, the sensor database or a default; always records its source |
| `photofootprint/camera/sensors.json` | No | Sensor widths for cameras that write a real focal length but no 35 mm equivalent |
| `photofootprint/importer/exif.py` | No | Pure-Python EXIF/XMP reader for JPEG and HEIC |
| `photofootprint/importer/scan.py` | No | Folder scan → `PhotoRecord`s, with skipped photos and reasons |
| `photofootprint/importer/algorithm.py` | Yes | Processing algorithm *Import photos*: writes the photo point layer |
| `photofootprint/styles.py` | Yes | Default layer styles |
| `photofootprint/compat.py` | Yes | Names that differ between QGIS 3.34 (Qt5) and QGIS 4 (Qt6) |
| `photofootprint/provider.py`, `plugin.py` | Yes | Processing provider and plugin entry point |
| `spike/viewshed_spike.py` | No (GDAL only) | Speed test for the viewshed footprint method |

Rule: maths and file parsing never import QGIS, so they can be unit-tested anywhere.

## Coordinate conventions

- **World frame:** x = east, y = north, z = up, in metres in a projected CRS. Photos are converted from WGS84 at import.
- **Camera frame:** x = right, y = forward (optical axis), z = up.
- **Angles (degrees):** heading clockwise from north; pitch positive up; roll positive when the right side of the image drops.
- **Pixels:** continuous coordinates, (0, 0) is the top-left corner of the image, and the centre of pixel (i, j) is (i + 0.5, j + 0.5).

The camera-to-world rotation is `R = Rz(heading) · Rx(pitch) · Ry(roll)`, applied roll first. A pinhole pixel (u, v) becomes the camera-frame direction `(u − cx, f, cy − v)`, where f is the focal length in pixels. A 360 pixel becomes yaw = (u/W − 0.5)·360°, pitch = (0.5 − v/H)·180°, with the image centre looking along the heading.

## Field of view

In order of preference:

1. Real focal length and a sensor width from `sensors.json` → `focal+sensor`
2. Real focal length and the EXIF focal plane resolution → `focal_plane_res`
3. 35 mm equivalent focal length, measured on the diagonal (43.27 mm) → `35mm`
4. The default (26 mm equivalent, a typical phone main camera) → `default`, with a note on the photo

## Footprints (M2, planned)

From the spike: GDAL's `ViewshedGenerate` runs on a window around the camera out to max range. A NumPy mask then keeps cells within heading ± HFOV/2 and pitch ± VFOV/2. This takes about 39 ms per photo on a 1 m DSM out to 300 m.
