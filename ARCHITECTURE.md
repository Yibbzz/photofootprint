# Architecture

## Layout

| Path | QGIS needed? | Responsibility |
| --- | --- | --- |
| `photofootprint/camera/model.py` | No | Pinhole and equirectangular camera models: pixel → 3D ray, 3D point → pixel |
| `photofootprint/camera/intrinsics.py` | No | Field of view from EXIF, the sensor database or a default; always records its source |
| `photofootprint/camera/sensors.json` | No | Sensor widths for cameras that write a real focal length but no 35 mm equivalent |
| `photofootprint/importer/exif.py` | No | Pure-Python EXIF/XMP reader for JPEG and HEIC |
| `photofootprint/importer/scan.py` | No | Folder scan → `PhotoRecord`s, with skipped photos and reasons |
| `photofootprint/camera/attrs.py` | No | Camera from a photo layer's attributes (hfov, heading, pitch, roll, height_agl) |
| `photofootprint/terrain/dem.py` | No (GDAL) | Windowed elevation reads, ground height under a camera |
| `photofootprint/footprint/compute.py` | No (GDAL) | Viewshed + camera frame mask → visible and raw footprint polygons |
| `photofootprint/footprint/algorithm.py` | Yes | Processing algorithm *Generate footprints* |
| `photofootprint/ui/footprint_dock.py` | Yes | Footprint panel: live footprint with heading/pitch sliders |
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

## Footprints

`footprint/compute.py` works in two steps, so the live slider only repeats the cheap part.

1. **`prepare()`** runs once per photo, elevation model and range. It reads a DEM window out to max range and finds the ground height under the camera. That comes from the DTM if one is given, otherwise from the lowest DSM cell within 2 m, so a GPS point that lands on a roof still starts at street level. It then runs GDAL's viewshed with the camera `height_agl` above that ground. It also stores every in-range cell's surface point relative to the camera.
2. **`footprint()`** runs for each heading/pitch/roll. It rotates the stored points into the camera frame and keeps those inside the image frame. That gives the **raw** footprint. ANDing it with the viewshed gives the **visible** footprint. Both are turned into polygons with `gdal.Polygonize`.

Testing a cell's surface point against the real camera model keeps roll and the curved frame edges of tilted cameras exact. Wherever the top of the frame is above the horizon, the footprint runs out to max range, and `capped` records that.

GDAL quirks handled: the viewshed output is cropped to its search extent, so it's pasted back onto the window grid. GDAL marks the observer's own cell and its neighbours unseen, so cells within two cells of the camera always count as seen. GDAL searches slightly past max range, so our own distance test sets the edge.

Occlusion is off by default for DEMs coarser than 5 m.

**Measured on a 1 m DEM (this machine):**

| Range | New photo (viewshed) | Slider move |
| --- | --- | --- |
| 300 m | ~45 ms | ~20 ms |
| 500 m | ~310 ms | ~55 ms |

Not yet done: the first footprint for a newly selected photo runs on the UI thread, so a long range can stall the UI briefly (about 0.3 s at 500 m). Moving it to a `QgsTask` is planned with the live zoom footprint in M4.
