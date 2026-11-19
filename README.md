# PhotoFootprint

A QGIS plugin that answers **"which of my site photos shows this spot?"**

It reads the GPS position, compass heading and lens data from geotagged photos (normal and 360) and works out each photo's field of view. Later versions will draw terrain-aware footprints clipped by buildings and terrain, link the photo viewer to the map both ways, and find the photos that show a clicked point.

See [BRIEF.md](BRIEF.md) for the full plan and [ARCHITECTURE.md](ARCHITECTURE.md) for how the code fits together.

## Status

Early development (0.1.0, milestone M1).

| Works now | Coming next |
| --- | --- |
| Import a folder of JPEG/HEIC photos into a point layer | Viewshed footprints on a DEM/DSM (M2) |
| Field of view from EXIF (35 mm equivalent, focal length + sensor, or a visible default) | Linked image viewer and reverse lookup (M3) |
| 360 photos detected from GPano XMP or a 2:1 aspect ratio | Heading correction (M4) |
| Photos without GPS are skipped and listed, never fatal | 360 viewer (M5) |
| Arrow symbols rotated by heading | |

## Install (development)

```bash
python3 scripts/package.py      # writes dist/photofootprint-<version>.zip
```

In QGIS: **Plugins → Manage and Install Plugins → Install from ZIP**, then **Plugins → PhotoFootprint → Import photos…** The same tool is in the Processing Toolbox under *PhotoFootprint*.

Requires QGIS 3.34 or later (Qt5 or Qt6). It uses only what ships with QGIS.

## Tests

```bash
python3 -m pytest
```

The camera and EXIF tests need only Python and NumPy. The import tests run inside a headless QGIS and are skipped when QGIS isn't installed. CI runs everything on QGIS 3.34 and the latest QGIS.

## Licence

GPL-3.0-or-later.
