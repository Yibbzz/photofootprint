"""Terrain-aware photo footprints. GDAL + NumPy only, no QGIS.

Two steps, so the live heading slider stays fast:

1. prepare(): read the DEM window around the camera and find the ground
   height. Depends only on position, height and range.
2. footprint(): mask the cells to the camera's frame for a given
   heading/pitch/roll, make sure visibility is known for the directions the
   frame covers, and turn the result into polygons.

Visibility (terrain/visibility.py) is computed per 1/32 sector of the circle,
only for sectors a frame has needed, and cached. A normal photo needs about a
fifth of the circle; turning the heading slider fills in more as it goes.

A cell counts as seen if the point at the centre of its surface is inside
the image frame (raw footprint) and is visible from the camera (visible
footprint). Using the camera model for the frame test keeps roll and the
curved frame edges of pitched cameras exact.
"""

import time
from dataclasses import dataclass
from typing import Optional

import numpy as np
from osgeo import gdal, ogr

from ..camera.model import Pose
from ..terrain.dem import Dem, Window
from ..terrain.visibility import N_SECTORS, sector_of, sweep

gdal.UseExceptions()
ogr.UseExceptions()

COARSE_DEM_M = 5.0           # occlusion is off by default for coarser DEMs
GROUND_SEARCH_M = 2.0        # radius for the lowest-cell ground estimate on a DSM


class Prepared:
    """Everything about one camera position that doesn't depend on its angles."""

    def __init__(self, x, y, ground_z, ground_source, height_agl, max_range, window,
                 occlusion, surface, in_range, dist):
        self.x, self.y = x, y
        self.ground_z = ground_z
        self.ground_source = ground_source          # "dtm" or "dsm_lowest_2m"
        self.height_agl = height_agl
        self.max_range = max_range
        self.window = window
        self.occlusion = occlusion
        self.surface = surface                      # (rows, cols, 3) cell centre surface points
        self.in_range = in_range                    # bool, valid cells within max range
        self.dist = dist
        # In-range cells relative to the camera, so each heading change only rotates them
        self.cells = np.flatnonzero(in_range)
        self.rel = np.ascontiguousarray(
            surface.reshape(-1, 3)[self.cells] - np.array([x, y, self.camera_z]))
        az = np.arctan2(self.rel[:, 0], self.rel[:, 1])
        self.cell_sector = sector_of(az)            # sector of each in-range cell
        self._done = np.zeros(N_SECTORS, dtype=bool)
        self._seen = np.zeros(in_range.shape, dtype=bool)
        if not occlusion:
            self._seen[:] = True
            self._done[:] = True
        else:
            self._seen |= dist <= 2 * window.res    # the ground at the camera's feet
            z = window.z.copy()
            z[window.cell_of(x, y)] = ground_z      # observer on the ground, not on a roof cell
            self._sweep_window = Window(z, window.gt, window.crs_wkt)

    @property
    def camera_z(self) -> float:
        return self.ground_z + self.height_agl

    def ensure_sectors(self, sectors) -> None:
        sectors = {int(k) % N_SECTORS for k in sectors}
        todo = sorted(k for k in sectors if not self._done[k])
        if todo:
            sweep(self._sweep_window, self.x, self.y, self.camera_z, self.max_range, todo, self._seen)
            self._done[todo] = True
        self._seen &= ~np.isnan(self.window.z)

    def seen_for(self, raw_flat_ok: np.ndarray) -> np.ndarray:
        """Visibility array, computed for every sector the in-frame cells touch (and neighbours)."""
        needed = np.unique(self.cell_sector[raw_flat_ok])
        self.ensure_sectors(np.concatenate([needed - 1, needed, needed + 1]))
        return self._seen

    @property
    def seen_from_camera(self) -> np.ndarray:
        """Full-circle visibility (computes any missing sectors)."""
        self.ensure_sectors(range(N_SECTORS))
        return self._seen


@dataclass
class Footprint:
    visible_wkt: Optional[str]
    raw_wkt: Optional[str]
    visible_area_m2: float
    raw_area_m2: float
    visible_pct: float
    capped: bool               # the visible area reaches max range
    dem_res_m: float
    ground_z: float
    ground_source: str
    occlusion: bool


def prepare(dem: Dem, x: float, y: float, height_agl: float, max_range: float,
            dtm: Optional[Dem] = None, occlusion: Optional[bool] = None) -> Prepared:
    """Raise ValueError with a user-facing reason if the camera can't be used."""
    if occlusion is None:
        occlusion = dem.res <= COARSE_DEM_M
    win = dem.window(x, y, max_range)
    if not win.contains(x, y):
        raise ValueError("camera is outside the elevation raster")

    ground, source = None, None
    if dtm is not None:
        try:
            ground = dtm.window(x, y, 2 * dtm.res).value_at(x, y)
            source = "dtm"
        except ValueError:
            ground = None
    if ground is None:
        ground = win.lowest_near(x, y, max(GROUND_SEARCH_M, win.res))
        source = "dsm_lowest_2m"
    if ground is None:
        raise ValueError("no elevation data under the camera")

    xs, ys = win.cell_centres()
    gx, gy = np.meshgrid(xs, ys)
    dist = np.hypot(gx - x, gy - y)
    valid = ~np.isnan(win.z)
    in_range = valid & (dist <= max_range)
    surface = np.stack([gx, gy, np.where(valid, win.z, ground)], axis=-1)
    return Prepared(x, y, ground, source, height_agl, max_range, win, occlusion,
                    surface, in_range, dist)


def _frame_ok(prep: Prepared, camera, heading, pitch, roll):
    pose = Pose(prep.x, prep.y, prep.camera_z, heading, pitch, roll)
    _, _, ok = camera.project_cam(prep.rel @ pose.matrix)   # world -> camera frame
    return ok


def frame_mask(prep: Prepared, camera, heading: float, pitch: float, roll: float = 0.0):
    """Bool array: cells whose surface point is inside the image frame and in range."""
    mask = np.zeros(prep.in_range.shape, dtype=bool)
    mask.flat[prep.cells] = _frame_ok(prep, camera, heading, pitch, roll)
    return mask


def footprint(prep: Prepared, camera, heading: float, pitch: float, roll: float = 0.0,
              polygons: bool = True) -> Footprint:
    ok = _frame_ok(prep, camera, heading, pitch, roll)
    raw = np.zeros(prep.in_range.shape, dtype=bool)
    raw.flat[prep.cells] = ok
    vis = raw & prep.seen_for(ok)
    cell_area = prep.window.res ** 2
    raw_area = float(raw.sum()) * cell_area
    vis_area = float(vis.sum()) * cell_area
    edge = prep.max_range - 1.5 * prep.window.res
    capped = bool(np.any(vis & (prep.dist > edge)))
    return Footprint(
        visible_wkt=polygonize(vis, prep.window) if polygons else None,
        raw_wkt=polygonize(raw, prep.window) if polygons else None,
        visible_area_m2=vis_area,
        raw_area_m2=raw_area,
        visible_pct=100.0 * vis_area / raw_area if raw_area else 0.0,
        capped=capped,
        dem_res_m=prep.window.res,
        ground_z=prep.ground_z,
        ground_source=prep.ground_source,
        occlusion=prep.occlusion,
    )


def _memory_vector_driver():
    for name in ("MEM", "Memory"):
        drv = ogr.GetDriverByName(name)
        if drv is not None:
            try:
                return drv.CreateDataSource("")
            except Exception:
                continue
    raise RuntimeError("no in-memory OGR driver available")


def polygonize(mask: np.ndarray, win: Window) -> Optional[str]:
    """MultiPolygon WKT of the True cells, or None if there are none."""
    if not mask.any():
        return None
    rows = np.nonzero(mask.any(axis=1))[0]
    cols = np.nonzero(mask.any(axis=0))[0]
    r0, r1, c0, c1 = rows[0], rows[-1] + 1, cols[0], cols[-1] + 1
    sub = mask[r0:r1, c0:c1].astype(np.uint8)
    gt = win.gt
    ds = gdal.GetDriverByName("MEM").Create("", sub.shape[1], sub.shape[0], 1, gdal.GDT_Byte)
    ds.SetGeoTransform((gt[0] + c0 * gt[1], gt[1], 0.0, gt[3] + r0 * gt[5], 0.0, gt[5]))
    band = ds.GetRasterBand(1)
    band.WriteArray(sub)

    vds = _memory_vector_driver()
    layer = vds.CreateLayer("fp", geom_type=ogr.wkbPolygon)
    layer.CreateField(ogr.FieldDefn("v", ogr.OFTInteger))
    gdal.Polygonize(band, band, layer, 0, [])
    multi = ogr.Geometry(ogr.wkbMultiPolygon)
    for f in layer:
        multi.AddGeometry(f.GetGeometryRef())
    return multi.ExportToWkt()


def timed_footprint(prep, camera, heading, pitch, roll=0.0):
    """footprint() plus elapsed milliseconds, for the live slider and reports."""
    t0 = time.perf_counter()
    fp = footprint(prep, camera, heading, pitch, roll)
    return fp, (time.perf_counter() - t0) * 1000.0
