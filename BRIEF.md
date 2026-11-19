# Project Brief: QGIS Photo Footprint & Linked Viewer Plugin

Nov 14, 2026 · @Nathan · revised Nov 19, 2026

## Overview and goal

Build a free, open-source QGIS plugin that answers **"which of my photos shows this spot?"** It places each site photo's view on an elevation model, clips it wherever buildings or terrain block the view, and links the photo to the map both ways.

**Positioning.** Lead with finding photos (reverse lookup and the linked viewer), not with exact footprints. Phone GPS and compass data are too noisy to promise "exactly what each photo sees". They are good enough to reliably answer "which photos cover this place, best first", and that is the feature no free tool offers today.

**Problem.** Today, photos on a GIS map are a point plus a generic view cone. The cone is drawn by hand or at a fixed angle, so it isn't based on the camera, the lens or the ground. It overstates coverage and ignores anything that blocks the view.

**Who it's for.** Site surveyors, planners, engineers, construction teams and heritage or environmental consultants who take lots of geotagged photos (normal and 360) and need to know which photo shows which part of a site.

**What exists already** (checked Nov 19, 2026). Free plugins such as Mapupdate360, Panorama Viewer and the Equirectangular 360° Viewer show 360 images with a direction sector. Photo viewpoint, Photo2GeoJSON and Advanced Photo Importer draw a fixed-angle cone or a rotated camera symbol, with no terrain. Pic2Map and moniQue do accurate monoplotting on a DTM, but only one photo at a time with control points, aimed at historical or scientific images. Visibility Analysis can already compute a camera-shaped viewshed if you set its azimuth and angle limits by hand. ArcGIS Oriented Imagery does the full linked experience, but it needs an ArcGIS licence. No free tool does batch footprints from EXIF, a linked viewer and a reverse lookup together. **The new value is the linked viewer and the reverse lookup.** The occluded footprint is a means to that, not the headline.

**Working name:** PhotoFootprint (the agent may suggest a better one).

## Prior art and reuse

Don't build from scratch: every core piece of this plugin already exists as free, open code. The new work is combining them into one QGIS workflow. Before writing any component, study (and where the licence allows, reuse) the tool listed for it below. Check each licence for compatibility with the plugin's own licence before copying code.

**Mapillary stack — the main reuse target**

- **Caveat on the Mapillary stack:** MapillaryJS has had no meaningful releases since about 2022, and OpenSfM is compiled C++ that is hard to install on Windows. Treat both as optional extras, never as core dependencies (see "Revised plan" below).
- **OpenSfM** ([docs](https://opensfm.readthedocs.io/)) is Mapillary's open-source structure-from-motion engine. Mapillary uses it to [correct noisy GPS positions](https://forum.mapillary.com/t/how-does-mapillary-correct-gps-location/7193) and replace bad phone compass headings with a [computed angle](https://forum.mapillary.com/t/view-direction-incorrect-with-5-13-android/7236) (`computed_compass_angle`). Use it locally (optional dependency or separate step) to solve camera position, heading, pitch and roll from overlapping site photos. This is the automatic version of requirement 9, heading correction. It needs overlap between photos, and 360 images help most. Its [camera coordinate conventions](https://mapillary.github.io/mapillary-js/docs/theory/coordinates) should be the basis for the camera module.
- **MapillaryJS** ([repo](https://www.github.com/mapillary/mapillary-js/releases)) is Mapillary's open-source image viewer for perspective, fisheye and 360 images. It can [run on your own images through a custom data provider](https://docsearch.algolia.com/mcp/docs/repo/mapillary/mapillary-js), with no Mapillary cloud needed, and it exposes `getPosition()`, `getFieldOfView()` and change events for two-way linking to a map. Evaluate embedding it in a QWebEngineView as the linked viewer (requirements 6 and 8) before building a custom one; that would also cover sequence navigation and 360.
- **Mapillary platform (optional).** If a user is happy for photos to be public, upload them and pull back the computed positions and headings via the API. Never upload by default, since site photos are often confidential.
- **Prior experiment:** Mapillary's own [viewshed experiment in ArcGIS Online](https://blog.mapillary.com/product/2017/03/06/mapillary-viewshed-arcgis-online.html) named "slice the viewshed to the camera's field of view" as the next step. That step is this plugin.

**QGIS plugins**

| Tool | Reuse for | Notes |
| --- | --- | --- |
| [Visibility Analysis](https://github.com/zoran-cuckovic/QGIS-visibility-analysis) (GPLv3) | Viewshed occlusion (req. 4) | Already supports [azimuth start/stop and upper/lower view angles](https://landscapearchaeology.org/?p=349) per observer. Set these from heading ± HFOV/2 and pitch ± VFOV/2 to get a camera-shaped viewshed. Call its Processing algorithms or reuse its fast algorithm. |
| [Footprint Generator](https://plugins.qgis.org/plugins/FootprintGenerator_v1_1_13/) | Ray–DTM footprints (req. 3) | Builds footprints from camera intrinsics and orientation, intersecting rays with a DTM. Built for aerial photogrammetry (omega/phi/kappa); adapt the approach to phone EXIF. |
| [Pic2Map](https://github.com/tproduit/pic2map) | Linked image↔map, monoplotting, pose from control points (req. 6, 9) | Proven design for click-in-image → map and overlaying map vectors on the photo. A [Qt6 fork](https://github.com/iggroup/pic2map) exists, but with features removed; the original is [seeking funding](https://github.com/tproduit/pic2map). Treat it as a reference, not a dependency. |
| [moniQue](https://github.com/smfloery/moniQue) | Pose from control points, monoplotting with uncertainty (req. 9) | Current research plugin (2026 paper) for oblique and historical images on a DTM mesh. One photo at a time; needs open3d/pygfx; listed as working only up to QGIS 3.34.3. Use as a reference for pose solving and uncertainty. |
| [Photo viewpoint](https://github.com/Riccardocasoli/photo-viewpoint) (GPL-3) | Viewpoint editing UI, print layout callouts | Fixed-angle 2D cones with editable direction and FOV, no terrain. Actively maintained. A UI reference, and shows the demand. |
| [Camera2Geo](https://pypi.org/project/camera2geo) (AGPL v3) | EXIF parsing, camera database, DEM projection | Pure-Python ExifRead + XMP parsing and a lens/camera lookup. Drone-focused and QGIS 4 only. **AGPL: study it, don't copy code.** |
| [Mapupdate360](https://plugins.qgis.org/plugins/Mapupdate360/) | 360 viewer, sequence navigation, heading/mount offsets (req. 8) | Shows a live frustum for 360 images. Unclear whether it uses a DEM; check the source. |
| [Panorama Viewer](https://plugins.qgis.org/plugins/panorama_viewer/) / [Equirectangular 360° Viewer](https://plugins.qgis.org/plugins/QgisGLViewer) | 360 display inside QGIS | Pannellum/three.js and OpenGL approaches. Panorama Viewer needs Qt5 WebKit, so avoid that route. |

**Other open projects**

- **[Panoramax](https://wiki.openstreetmap.org/wiki/Panoramax)** — open-source, self-hostable street-level imagery server and viewer. Use it as an optional backend for teams that want to host site photos on their own server.
- **[Smapshot](https://isprs-archives.copernicus.org/articles/XLVIII-4-W1-2022/217/2022/isprs-archives-XLVIII-4-W1-2022-217-2022.pdf)** and the WSL monoplotting tool — open research projects doing pose-from-control-points and photo footprints in a web 3D globe. Use them as design references for the UI and the footprint API.
- **[Pannellum](https://alternativeto.net/feature/panorama-viewer/)** — lightweight open 360 web viewer, the fallback if MapillaryJS doesn't fit.

**Proposed build-vs-reuse split**

| Requirement | Approach |
| --- | --- |
| 1 Import | Adapt Camera2Geo-style ExifRead + XMP parsing |
| 2 Camera model | Follow OpenSfM conventions |
| 3 Footprints | Viewshed wedge first (req. 4); edge ray casting only as a refinement for photos tilted down, informed by Footprint Generator |
| 4 Occlusion | Reuse Visibility Analysis with azimuth/angle limits |
| 5 Elevation fallback | Cut to a "where to get a DEM" help page and source list; no auto-download |
| 6 Linked viewer | Custom Qt viewer (QGraphicsView) as the main path; MapillaryJS only as an optional extra where QWebEngine is available |
| 7 Reverse lookup | New |
| 8 360 | Pannellum or three.js in QWebEngineView where available |
| 9 Heading correction | Manual slider and two-point correction (Pic2Map-style) in the MVP; OpenSfM as an optional outside step later |

## Revised plan: why the scope changed

A review of the original brief found these problems. The scope below is built around them.

1. **For ground-level photos, the footprint is mostly the viewshed.** At 1.6 m eye height and 0° pitch, about the top half of the rays never hit the ground and get capped at max range. Edge ray casting adds little over a camera-limited viewshed except for photos tilted down. So the MVP uses the viewshed wedge (heading ± HFOV/2, pitch ± VFOV/2) as the footprint.
2. **Pitch and roll are usually unknown.** Most phone EXIF (iPhone included) records a compass heading but not tilt. Defaulting to 0° when people often shoot slightly downwards moves the near edge a lot. Show the assumed pitch, make it easy to change, and lower the confidence rating when it's a default.
3. **Compass error is bigger than the occlusion detail.** A 15° heading error moves the footprint about 26 m sideways at 100 m. Heading correction (a manual slider plus two-point correction) therefore moves into the MVP. Without it, the M3 test ("within 2 m") only passes with a corrected heading.
4. **A DSM is 2.5D and has no building faces.** Rays aimed at a façade land on the roof edge or the ground in front of it. Document this as a known limitation for façade and heritage work. Reverse lookup should still return façade photos when the clicked point is at the façade's base.
5. **Target users.** Construction 360 walkthroughs are mostly handled by OpenSpace, HoloBuilder and StructionSite (indoor, floor-plan based). Aim first at outdoor site, environmental, highways and utility surveyors, and at landscape visual impact assessment (LVIA) work.
6. **Fragile dependencies.** MapillaryJS is effectively unmaintained, and QWebEngine is missing from some QGIS builds, especially older Qt5 ones on Windows. OpenSfM is hard to install. The core plugin must work with neither.
7. **Licensing.** Copying Visibility Analysis code (GPLv3) makes the whole plugin GPLv3, and Camera2Geo (AGPL-3) can't be copied in at all. The plugin is licensed GPL-3.0-or-later.
8. **Scope size.** Automatic elevation download is a project of its own, so it's cut to documentation.

## Scope

The MVP ships the most useful features first, and it holds up to bad compass data. It runs on ordinary (perspective) photos with a user-supplied DEM/DSM.

| Phase | Includes |
| --- | --- |
| 1 — MVP | EXIF import to a photo point layer; camera model from EXIF with visible, editable defaults; footprint as a camera-limited viewshed wedge on a user DEM/DSM (occlusion included); dockable Qt image viewer with click-in-image → map point; reverse lookup (click map → photos that see it, ranked); manual heading slider with live footprint update |
| 2 — Accuracy | Two-point heading (and optional pitch) correction; live footprint for the zoomed or panned part of the image; map hover → crosshair in image; edge ray-cast refinement for photos tilted down; batch Processing algorithms; confidence rating per footprint |
| 3 — 360 | Equirectangular 360 viewer (QWebEngineView where available) with live yaw/pitch/zoom footprint; full 360 coverage footprint |
| 4 — Optional extras | OpenSfM heading solve as an outside step; MapillaryJS viewer; elevation source help page |

**Out of scope (for now):** automatic DEM download, full photogrammetry or structure-from-motion inside the plugin, video, drone flight planning, 3D mesh output, cloud hosting, and any paid data source.

## Functional requirements

**1. Photo import**

- Import a folder of JPEG/HEIC photos (recursive optional) into a point layer.
- Read from EXIF/XMP: GPS lat/lon/altitude, GPSImgDirection (heading), focal length, 35 mm equivalent focal length, image size, timestamp, camera make/model, and GPano XMP tags for 360 images (ProjectionType, PoseHeadingDegrees).
- Flag each photo as `perspective` or `360` automatically (GPano tags or a 2:1 aspect ratio).
- Fill missing values from a camera/sensor database. Otherwise use user defaults (e.g. eye height 1.6 m, tilt 0°).
- Let users edit any camera parameter per photo in the attribute table.

**2. Camera model**

- Pinhole model for perspective photos: horizontal and vertical FOV from focal length and sensor size (or the 35 mm equivalent).
- Pose = position (projected CRS), height above ground, heading (azimuth), pitch, roll. Default pitch and roll to 0 unless XMP gives them.
- Equirectangular model for 360: every pixel maps to (yaw, pitch).
- One shared function: `pixel → 3D ray` and `3D point → pixel (or not visible)`.

**3. Terrain-projected footprints**

- Cast rays from the camera through a grid of image-edge pixels (e.g. 32–64 along each edge) and intersect them with the DEM.
- Rays that never hit the ground within a max range (default 500 m, user-set) get capped at that range, and the cap is flagged.
- Build a footprint polygon from the hits. Save it to a footprint layer linked to the photo by ID.

**4. Viewshed occlusion**

- With a DSM (surface model that includes buildings and trees), clip the footprint to the cells actually visible from the camera.
- Use QGIS/GDAL viewshed (`gdal_viewshed` or the QGIS Visibility Analysis tools) limited to the footprint's bounding box and max range, then intersect.
- Output: a visible footprint (clipped) and the raw footprint, both kept.

**5. Elevation handling**

- The user picks any DEM/DSM raster already in the project (preferred: site LiDAR).
- If none is given, point them to a help page listing free sources: England — Environment Agency National LIDAR Programme 1 m DTM/DSM; Scotland — Scottish Remote Sensing Portal LiDAR; Wales — DataMapWales LiDAR; USA — USGS 3DEP; global fallback — Copernicus GLO-30. Automatic download is out of scope.
- Always record which source and resolution a footprint used. Warn when it's coarser than 5 m, and disable occlusion by default below that resolution.

**6. Linked image viewer (the ArcGIS-style experience)**

- Dockable panel inside QGIS showing the selected photo, with zoom, pan and next/previous along a sequence.
- As the user zooms or pans, recompute the footprint of the visible part of the image and redraw it live on the map canvas as a highlight (QgsRubberBand). Throttle it so it stays smooth.
- Click a pixel in the image → cast a ray → drop a marker on the map at the ground hit, with coordinates and distance.
- Hover on the map → project that point into the current image and show a crosshair, or "not visible / occluded".
- Selecting a photo point or footprint on the map opens it in the viewer, and the map highlights the current photo.

**7. Reverse lookup**

- Map tool: click a location (or draw a polygon) → list every photo whose visible footprint covers it.
- Rank them by distance, how central the point sits in the frame, and estimated ground resolution (cm per pixel). The best photo opens first, with the point marked in the image.

**8. 360 support**

- Viewer for equirectangular images, using a Qt6 QWebEngineView with Pannellum or three.js (with a fallback if WebEngine is missing).
- The visible window (yaw, pitch, FOV) drives the live footprint, in the same way as perspective photos.
- A full 360 coverage footprint for batch output: rays around the whole horizon, clipped by the viewshed.

**9. Heading correction**

- Phone compass headings can be off by 10–20°. Tool: the user clicks a feature in the image and the same feature on the map (or 2+ pairs), and the plugin solves for heading (and optionally pitch).
- Also allow a manual heading slider with the footprint updating live.

## Technical approach and architecture

**Platform.** A Python plugin for QGIS 3.34 LTR and later. It must run on Qt6 builds and QGIS 4 as well as Qt5, so don't use QtWebKit. Use `qgis.PyQt` imports only. Target Windows, macOS and Linux.

**Dependencies.** Only what ships with QGIS: NumPy, GDAL/OGR, PyQGIS, processing. Read EXIF with Pillow if present, falling back to `exifread` or a vendored pure-Python parser. Use no compiled extras unless they're optional.

| Module | Responsibility |
| --- | --- |
| `importer/` | Scan folders, parse EXIF/XMP, write the photo point layer |
| `camera/` | Pinhole and equirectangular models; pixel ↔ ray; sensor database (JSON) |
| `terrain/` | DEM access (windowed reads via GDAL), ray–DEM intersection, height sampling, tile cache |
| `footprint/` | Edge ray casting, polygon building, viewshed clipping, batch Processing algorithms |
| `sources/` | DEM resolution checks and source attribution (no auto-download) |
| `viewer/` | Dock widget: QGraphicsView image viewer; QWebEngineView 360 viewer; sync signals |
| `maptools/` | Rubber-band highlight, click-to-map, map-hover crosshair, reverse-lookup tool, heading correction |
| `processing/` | QGIS Processing provider exposing import, footprints and reverse lookup for models and batch runs |

**Core maths.** Each pixel becomes a ray from the camera centre C. For a perspective photo with focal length f in pixels and principal point (cx, cy):

```latex
\mathbf{d} = R(\text{heading},\text{pitch},\text{roll}) \begin{bmatrix} u - c_x \\ f \\ c_y - v \end{bmatrix}, \quad \mathbf{p}(t) = \mathbf{C} + t\,\mathbf{d}
```

To hit the ground, march along t in steps of about half the DEM cell size until the ray drops below the terrain, then refine by bisection. Work in a projected metric CRS (e.g. EPSG:27700 for the UK); convert from WGS84 at import. Ignore earth curvature and refraction below 1 km, and apply the standard correction beyond that.

**Viewer sync.** The viewer emits `visibleRegionChanged(photo_id, pixel_rect or yaw/pitch/fov)`. The footprint service recomputes on a background QgsTask, debounced to about 100 ms, and the map tool redraws the rubber band. The UI must never block on raster reads.

## Data model and outputs

Everything is stored in one GeoPackage per project, so it can be shared and opened without the plugin.

| Layer | Geometry | Key fields |
| --- | --- | --- |
| `photos` | Point | photo\_id, path (relative), type (perspective/360), datetime, x, y, z, height\_agl, heading, pitch, roll, hfov, vfov, focal\_mm, sensor\_w\_mm, heading\_source (exif/corrected/manual), status |
| `footprints_raw` | Polygon | photo\_id, dem\_source, dem\_res\_m, max\_range\_m, capped (bool), area\_m2 |
| `footprints_visible` | MultiPolygon | photo\_id, dem\_source, dem\_res\_m, visible\_pct, created |
| `camera_rays` (optional) | Line | photo\_id, pixel\_u, pixel\_v, hit (bool) — for debugging |

- Default styles (QML) are shipped: camera symbol rotated by heading, translucent footprints.
- The photo point layer gets a map tip or form showing a thumbnail.
- Export: GeoJSON or KML of footprints, and a CSV of the reverse-lookup results.

## Performance, accuracy and constraints

- **Speed targets:** the live footprint redraws in under 150 ms on a 1 m DEM within 300 m range; a batch of 500 photos with occlusion runs in under 10 minutes on a normal laptop.
- **Windowed reads only:** never load a whole DEM. Read the window around the camera out to max range, and cache it per photo.
- **Accuracy is limited by inputs, so show them:** phone GPS (±3–10 m), compass (±10–20°) and DEM resolution dominate the error. Record every input's source and surface a simple confidence rating (high / medium / low) per footprint.
- **Unknown values:** never invent them silently. Defaults must be visible in the attributes and editable.
- **Privacy:** photos stay local. Downloads are only for elevation tiles, with a clear prompt the first time and an offline mode.
- **Licensing:** GPL-3.0-or-later, so GPLv3 code such as Visibility Analysis can be reused. Never copy AGPL code (Camera2Geo). Record and display attribution for each elevation source (e.g. Open Government Licence for EA data).
- **Stability:** no crashes on bad EXIF; skip and log the photo instead.

## Milestones and acceptance criteria

Each milestone ends with a working plugin zip that installs from "Install from ZIP" and a short demo on the test dataset.

1. **M1 — Import and camera model.** 50 test photos import with correct positions and headings; missing EXIF is reported, not fatal; assumed defaults (pitch, height) are visible in the attributes; pixel↔ray round-trip error under 0.5 px in unit tests.
2. **M2 — Viewshed footprints.** On flat synthetic terrain, the wedge matches the analytic shape within 1%. A box "building" in a synthetic DSM visibly cuts footprints behind it. The heading slider updates the footprint live.
3. **M3 — Linked viewer and reverse lookup (MVP release).** Selecting a photo opens it in the Qt viewer; clicking the image drops a map point. Clicking a map point returns the photos that show it, best first, with the point marked in the image. On the real test set, the correct photo is in the top 3 for at least 8 of 10 hand-picked points.
4. **M4 — Accuracy.** A two-point heading correction reduces the feature-placement error on the test set; clicking a known feature lands within 2 m on a 1 m DSM after correction. Zoom or pan updates the map highlight within 150 ms.
5. **M5 — 360.** 360 photos open in the viewer on Qt6 (where QWebEngine is available) with a live footprint; the plugin degrades gracefully where it isn't.
6. **M6 — Release.** Docs, example project, and submission to the QGIS plugin repository.

## Testing, deliverables and instructions for the agent

**Testing**

- Unit tests (pytest + `qgis.testing`) for the camera maths, ray–DEM intersection, polygon building and EXIF parsing.
- Synthetic fixtures: a flat plane, a slope, and a plane with one box "building" as DSMs, with exactly known answers.
- One real test set: about 50 phone photos and 5 360 photos of one site, plus a 1 m EA LiDAR DTM/DSM tile clipped to it. The project owner will supply the photos.
- CI on GitHub Actions using the official QGIS Docker image, on LTR and latest.

**Deliverables**

- A GitHub repository with the plugin source, tests, CI and a `metadata.txt` ready for the QGIS plugin repository.
- A README covering install, quick start and limitations, plus a short user guide with screenshots.
- An example QGIS project with the test data.
- `ARCHITECTURE.md` explaining the modules and the maths.

**How to work**

- Start with a short spike (about 1–2 days) proving the biggest bet: Visibility Analysis (or `gdal_viewshed`) with azimuth and angle limits giving a usable camera footprint on a 1 m DSM within the speed targets. Also check which QGIS builds (Windows OSGeo4W, macOS, Linux, Qt5 and Qt6) ship QWebEngine. Report back before M1.

* Build in milestone order. Don't start the next one until the current one's acceptance criteria pass.
* Keep the maths in pure Python/NumPy modules with no QGIS imports, so it can be tested without QGIS.
* Ask before adding any dependency that doesn't ship with QGIS.
* At the end of each milestone, report what works, what doesn't, the measured speed and accuracy against the targets, and any decisions that need the owner.
* If a requirement here turns out impractical (e.g. a speed target), say so with numbers and propose an alternative instead of quietly changing it.

**Open questions for the owner**

- [ ] Which test site and photos to use?
- [ ] Plugin name, and whether to publish under a personal or organisation GitHub account?
- [ ] Is QGIS 3.34 LTR the oldest version to support?
