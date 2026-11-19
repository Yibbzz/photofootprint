# PhotoFootprint

A QGIS plugin that answers **"which of my site photos shows this spot?"**

It reads the GPS position, compass heading and lens data from geotagged photos (normal and 360) and works out the ground each photo shows on an elevation model, minus whatever buildings and terrain hide. Click the map to get the photos that show that spot, best first. Click a photo to see where that spot is on the map.

See [BRIEF.md](BRIEF.md) for the full plan and [ARCHITECTURE.md](ARCHITECTURE.md) for how the code fits together.

## Status

Early development (0.3.0, milestone M3: the MVP features are in).

| Works now | Coming next |
| --- | --- |
| **Import photos**: a folder of JPEG/HEIC photos becomes a point layer, with field of view from EXIF (or a clearly marked default) | Two-point heading correction (M4) |
| **Generate footprints**: the ground each photo shows, with buildings and terrain cut out (visible and raw layers) | Live footprint of the zoomed-in part of a photo; map hover → crosshair in the photo (M4) |
| **Photo panel**: select a photo to view it, and its footprint is drawn on the map. Heading and pitch sliders redraw it live; Save stores them | 360 viewer (M5) |
| **Click in the photo**: a marker drops on the map where that pixel hits the ground, with its distance | |
| **Find photos at a map spot**: click the map to get the photos that show that spot, best first. The best one opens with the spot marked | |
| **Find photos at points**: the same for a whole point layer, as a table (save it as CSV) | |
| Compass headings (true north) are converted to the map grid automatically | |

## Install (development)

```bash
python3 scripts/package.py      # writes dist/photofootprint-<version>.zip
```

In QGIS: **Plugins → Manage and Install Plugins → Install from ZIP**. Then, from the **Plugins → PhotoFootprint** menu:

1. **Import photos…** Choose your photo folder.
2. **Generate footprints…** Choose the photo layer and an elevation model. A DSM with buildings gives occlusion. In England, the Environment Agency's free 1 m LiDAR DSM works well.
3. **Photo panel.** Select a photo on the map to see it with its footprint, or press **Find photos at a map spot** and click the map.
4. **Find photos at points…** Runs the lookup for every point in a layer.

The tools are also in the Processing Toolbox under *PhotoFootprint*, so they work in batch mode and the model builder.

Requires QGIS 3.34 or later (Qt5 or Qt6). It uses only what ships with QGIS.

## Tests

```bash
python3 -m pytest
```

The camera and EXIF tests need only Python and NumPy. The import tests run inside a headless QGIS and are skipped when QGIS isn't installed. CI runs everything on QGIS 3.34 and the latest QGIS.

## Licence

GPL-3.0-or-later.
