"""Terrain-aware photo footprints. GDAL + NumPy only, no QGIS.

Two steps, so the live heading slider stays fast:

1. prepare(): read the DEM window around the camera, find the ground height
   and run the viewshed. This depends only on position, height and range.
2. footprint(): mask the prepared cells to the camera's frame for a given
   heading/pitch/roll and turn the result into polygons.

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

gdal.UseExceptions()
ogr.UseExceptions()

REFRACTION_COEFF = 0.85714   # standard atmospheric refraction, GDAL's default
COARSE_DEM_M = 5.0           # occlusion is off by default for coarser DEMs
GROUND_SEARCH_M = 2.0        # radius for the lowest-cell ground estimate on a DSM


@dataclass
class Prepared:
    x: float
    y: float
    ground_z: float
    ground_source: str        # "dtm" or "dsm_lowest_2m"
    height_agl: float
    max_range: float
    window: Window
    seen_from_camera: np.ndarray   # bool, viewshed result (all True if occlusion is off)
    occlusion: bool
    surface: np.ndarray       # (rows, cols, 3) cell centre points on the surface
    in_range: np.ndarray      # bool, valid cells within max range
    dist: np.ndarray
    cells: np.ndarray = None  # flat indices of in-range cells
    rel: np.ndarray = None    # (n, 3) in-range surface points minus the camera centre,
                              # computed once so each heading change only rotates them

    @property
    def camera_z(self) -> float:
        return self.ground_z + self.height_agl


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

    if occlusion:
        z = win.z.copy()
        z[win.cell_of(x, y)] = ground   # observer stands on the ground, not on a roof cell
        src = win.to_mem(z)
        vs = _viewshed(src.GetRasterBand(1), x, y, height_agl, max_range + 2 * win.res)
        # GDAL searches slightly past max range; our own distance test sets the edge.
        seen = _paste(vs.GetRasterBand(1).ReadAsArray() == 1, vs.GetGeoTransform(), win)
        # GDAL marks the observer's own cell and its neighbours unseen; the ground
        # at the camera's feet is always visible.
        seen |= dist <= 2 * win.res
    else:
        seen = np.ones(win.z.shape, dtype=bool)

    surface = np.stack([gx, gy, np.where(valid, win.z, ground)], axis=-1)
    cells = np.flatnonzero(in_range)
    rel = surface.reshape(-1, 3)[cells] - np.array([x, y, ground + height_agl])
    return Prepared(x, y, ground, source, height_agl, max_range, win, seen & valid,
                    occlusion, surface, in_range, dist, cells, np.ascontiguousarray(rel))


def _viewshed(band, x, y, height, max_distance):
    """gdal.ViewshedGenerate in memory. Old GDAL (3.4) needs creationOptions=None;
    new GDAL (3.10) needs a list. Try one, then the other."""
    args = (x, y, height, 0.0, 1, 0, 0, 0, REFRACTION_COEFF, gdal.GVM_Edge, max_distance)
    try:
        return gdal.ViewshedGenerate(band, "MEM", "", [], *args)
    except TypeError:
        return gdal.ViewshedGenerate(band, "MEM", "", None, *args)


def _paste(arr, gt, win: Window):
    """Place a raster that GDAL cropped to the search range back onto the window grid."""
    out = np.zeros(win.z.shape, dtype=bool)
    c0 = int(round((gt[0] - win.gt[0]) / win.gt[1]))
    r0 = int(round((gt[3] - win.gt[3]) / win.gt[5]))
    rows, cols = arr.shape
    # Clip to the window in case GDAL's extent is larger
    sr0, sc0 = max(-r0, 0), max(-c0, 0)
    dr0, dc0 = max(r0, 0), max(c0, 0)
    h = min(rows - sr0, out.shape[0] - dr0)
    w = min(cols - sc0, out.shape[1] - dc0)
    if h > 0 and w > 0:
        out[dr0:dr0 + h, dc0:dc0 + w] = arr[sr0:sr0 + h, sc0:sc0 + w]
    return out


def frame_mask(prep: Prepared, camera, heading: float, pitch: float, roll: float = 0.0):
    """Bool array: cells whose surface point is inside the image frame and in range."""
    pose = Pose(prep.x, prep.y, prep.camera_z, heading, pitch, roll)
    mask = np.zeros(prep.in_range.shape, dtype=bool)
    _, _, ok = camera.project_cam(prep.rel @ pose.matrix)   # world -> camera frame
    mask.flat[prep.cells] = ok
    return mask


def footprint(prep: Prepared, camera, heading: float, pitch: float, roll: float = 0.0,
              polygons: bool = True) -> Footprint:
    raw = frame_mask(prep, camera, heading, pitch, roll)
    vis = raw & prep.seen_from_camera
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
