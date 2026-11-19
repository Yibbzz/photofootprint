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
| `photofootprint/terrain/visibility.py` | No | Radial-sweep visibility (which cells the camera can see), per sector on demand |
| `photofootprint/terrain/raycast.py` | No | Bilinear surface sampling, ray → surface hit, line of sight |
| `photofootprint/lookup/rank.py` | No | Which photos show a point, ranked; image pixel → ground point |
| `photofootprint/lookup/algorithm.py` | Yes | Processing algorithm *Find photos at points* |
| `photofootprint/geo.py` | Yes | True north → grid north (grid convergence) |
| `photofootprint/paths.py` | No | Resolve photo paths stored relative to the photo layer |
| `photofootprint/ui/image_view.py` | Yes | Zoomable photo view with click → pixel and a crosshair |
| `photofootprint/footprint/compute.py` | No (GDAL) | Viewshed + camera frame mask → visible and raw footprint polygons |
| `photofootprint/footprint/algorithm.py` | Yes | Processing algorithm *Generate footprints* |
| `photofootprint/ui/footprint_dock.py` | Yes | Photo panel: viewer, live footprint, sliders, image ↔ map, find photos |
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
- **True vs grid north:** the photo layer and sliders hold true bearings, as a compass gives them. `geo.true_north_offset` turns them into grid bearings before any geometry. The offset is about −1.5° in London on BNG and 0° on the grid's central meridian.
- **Pixels:** continuous coordinates, (0, 0) is the top-left corner of the image, and the centre of pixel (i, j) is (i + 0.5, j + 0.5).

The camera-to-world rotation is `R = Rz(heading) · Rx(pitch) · Ry(roll)`, applied roll first. A pinhole pixel (u, v) becomes the camera-frame direction `(u − cx, f, cy − v)`, where f is the focal length in pixels. A 360 pixel becomes yaw = (u/W − 0.5)·360°, pitch = (0.5 − v/H)·180°, with the image centre looking along the heading.

## Field of view

In order of preference:

1. Real focal length and a sensor width from `sensors.json` → `focal+sensor`
2. Real focal length and the EXIF focal plane resolution → `focal_plane_res`
3. 35 mm equivalent focal length, measured on the diagonal (43.27 mm) → `35mm`
4. The default (26 mm equivalent, a typical phone main camera) → `default`, with a note on the photo

## Visibility

`terrain/visibility.py` casts rays outward from the camera, about two per cell at max range, in half-cell steps over the bilinear surface. It keeps a running maximum of the horizon slope, and a point is visible if it reaches that horizon. A cell is visible if any sample in it is. This is the same geometry as `raycast.line_of_sight`, so the footprints and the photo lookup agree.

The circle is split into 32 sectors, computed only when a frame needs them and then cached. A normal photo needs about a fifth of the circle, and turning the heading slider fills in more as it goes.

**Why not `gdal.ViewshedGenerate`?** It was used in M2. GDAL uses the Wang et al. reference-plane approximation instead of true line of sight, and errs towards hidden. Against strict line of sight on test surfaces with buildings and hills, it wrongly hid 1–5% of points.

**Why not GRASS `r.viewshed`?** It is accurate: it uses an exact sweep-line algorithm and is within 0.1–0.4% of strict line of sight on the same tests. But it takes about 1.4 s per photo including GRASS start-up (about 12 min for 500 photos), it always does the full circle, and GRASS isn't in every QGIS install. Instead it serves as the independent reference: `tests/test_grass_crosscheck.py` requires the plugin's sweep to agree with `r.viewshed` on over 99% of cells, and runs wherever GRASS is installed. Measured: 0.04–0.08% seen only by GRASS, 0.55–0.69% only by the plugin. The second figure is the any-part-of-the-cell rule.

Line of sight is strict, with 0 m tolerance. An earlier 0.25 m allowance let sightlines graze through building edges and made 5–8% too much ground visible.

## Photo lookup

`lookup/rank.py`: a photo shows a point if the point (on the surface model, so a roof stays a roof) projects inside the frame, and the line from the camera to just above it (0.1 m) is clear. Matches are ranked by estimated detail at the point (cm per pixel) × (1 + distance from the image centre, 0 at the centre to 1 at a corner). Points near the edge are the first to fall out of the photo when the compass is a few degrees off.

Image → map casts the pixel's ray and finds the first surface hit by marching in half-cell steps, then bisecting.

## Footprints

`footprint/compute.py` works in two steps, so the live slider only repeats the cheap part.

1. **`prepare()`** runs once per photo, elevation model and range. It reads a DEM window out to max range and finds the ground height under the camera. That comes from the DTM if one is given, otherwise from the lowest DSM cell within 2 m, so a GPS point that lands on a roof still starts at street level. It stores every in-range cell's surface point relative to the camera.
2. **`footprint()`** runs for each heading/pitch/roll. It rotates the stored points into the camera frame and keeps those inside the image frame. That gives the **raw** footprint. It then makes sure visibility is known for the sectors the frame touches, and ANDs it in to give the **visible** footprint. Both are turned into polygons with `gdal.Polygonize`.

Testing a cell's surface point against the real camera model keeps roll and the curved frame edges of tilted cameras exact. Wherever the top of the frame is above the horizon, the footprint runs out to max range, and `capped` records that.

Occlusion is off by default for DEMs coarser than 5 m.

**Measured on a 1 m DEM (this machine):**

| Range | New photo (first footprint) | Slider move (worst) | 500-photo batch |
| --- | --- | --- | --- |
| 300 m | ~180 ms | ~40 ms | ~1.5 min |
| 500 m | ~600 ms | ~110 ms | ~5 min |

Not yet done: the first footprint for a newly selected photo, and loading the image, run on the UI thread, so a long range stalls the panel briefly (about 0.6 s at 500 m). Moving these to a `QgsTask` is planned with the live zoom footprint in M4.
