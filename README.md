# PhotoFootprint

A QGIS plugin that answers **"which of my site photos shows this spot?"**

It reads the GPS position, compass heading and lens data from geotagged photos (normal and 360), works out the ground each photo shows on an elevation model, and cuts out whatever buildings and terrain hide from the camera. Later versions will link the photo viewer to the map both ways and find the photos that show a clicked point.

See [BRIEF.md](BRIEF.md) for the full plan and [ARCHITECTURE.md](ARCHITECTURE.md) for how the code fits together.

## Status

Early development (0.2.0, milestone M2).

| Works now | Coming next |
| --- | --- |
| **Import photos**: a folder of JPEG/HEIC photos becomes a point layer, with field of view from EXIF (or a clearly marked default) | Linked image viewer and reverse lookup: click the map, get the photos that show it (M3) |
| **Generate footprints**: the ground each photo shows on a DEM/DSM, with buildings and terrain cutting out what the camera can't see (visible and raw layers) | Two-point heading correction (M4) |
| **Footprint panel**: select a photo, drag the heading and pitch sliders and the footprint redraws live; save the corrected values to the photo | 360 viewer (M5) |
| 360 photos get a full-circle footprint | |
| Bad or GPS-less photos are skipped with a reason, never fatal | |

## Install (development)

```bash
python3 scripts/package.py      # writes dist/photofootprint-<version>.zip
```

In QGIS: **Plugins → Manage and Install Plugins → Install from ZIP**. Then, from the **Plugins → PhotoFootprint** menu:

1. **Import photos…** Choose your photo folder.
2. **Generate footprints…** Choose the photo layer and an elevation model. A DSM with buildings gives occlusion. In England, the Environment Agency's free 1 m LiDAR DSM works well.
3. **Footprint panel.** Select a photo on the map to see and adjust its footprint live.

The tools are also in the Processing Toolbox under *PhotoFootprint*, so they work in batch mode and the model builder.

Requires QGIS 3.34 or later (Qt5 or Qt6). It uses only what ships with QGIS.

## Tests

```bash
python3 -m pytest
```

The camera and EXIF tests need only Python and NumPy. The import tests run inside a headless QGIS and are skipped when QGIS isn't installed. CI runs everything on QGIS 3.34 and the latest QGIS.

## Licence

GPL-3.0-or-later.
