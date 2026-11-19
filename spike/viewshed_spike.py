"""Spike: is GDAL viewshed + a NumPy camera mask fast enough for footprints?

Builds a synthetic 1 m DSM (flat ground plus one 10 m box building),
runs gdal.ViewshedGenerate in memory on a window around the camera,
then masks the result to the camera's horizontal and vertical field of view.

Run:  python3 -I spike/viewshed_spike.py
"""

import math
import time

import numpy as np
from osgeo import gdal, osr

gdal.UseExceptions()

CELL = 1.0          # metres
SIZE = 2001         # cells per side (2 km square)
GROUND = 50.0       # ground elevation, metres
EYE = 1.6           # camera height above ground
MAX_RANGE = 300.0   # metres


def make_dsm():
    z = np.full((SIZE, SIZE), GROUND, dtype=np.float32)
    c = SIZE // 2
    # 20 x 20 m building, 10 m tall, 50-70 m north of the camera
    z[c - 70:c - 50, c - 10:c + 10] = GROUND + 10.0
    ds = gdal.GetDriverByName("MEM").Create("", SIZE, SIZE, 1, gdal.GDT_Float32)
    ds.SetGeoTransform((0.0, CELL, 0.0, SIZE * CELL, 0.0, -CELL))
    srs = osr.SpatialReference()
    srs.ImportFromEPSG(27700)
    ds.SetProjection(srs.ExportToWkt())
    ds.GetRasterBand(1).WriteArray(z)
    return ds


def window(ds, x, y, radius):
    """Read a square window around (x, y) into a new MEM dataset."""
    gt = ds.GetGeoTransform()
    col = int((x - gt[0]) / gt[1])
    row = int((y - gt[3]) / gt[5])
    r = int(math.ceil(radius / gt[1]))
    c0, r0 = max(col - r, 0), max(row - r, 0)
    c1 = min(col + r + 1, ds.RasterXSize)
    r1 = min(row + r + 1, ds.RasterYSize)
    arr = ds.GetRasterBand(1).ReadAsArray(c0, r0, c1 - c0, r1 - r0)
    out = gdal.GetDriverByName("MEM").Create("", c1 - c0, r1 - r0, 1, gdal.GDT_Float32)
    out.SetGeoTransform((gt[0] + c0 * gt[1], gt[1], 0.0, gt[3] + r0 * gt[5], 0.0, gt[5]))
    out.SetProjection(ds.GetProjection())
    out.GetRasterBand(1).WriteArray(arr)
    return out, arr


def camera_footprint(ds, x, y, heading, pitch, hfov, vfov):
    win, z = window(ds, x, y, MAX_RANGE)
    args = (x, y, EYE, 0.0, 1, 0, 0, -1, 0.85714, gdal.GVM_Edge, MAX_RANGE)
    try:
        vs = gdal.ViewshedGenerate(win.GetRasterBand(1), "MEM", "", [], *args)
    except TypeError:   # GDAL 3.4 wants None instead of []
        vs = gdal.ViewshedGenerate(win.GetRasterBand(1), "MEM", "", None, *args)
    visible = vs.GetRasterBand(1).ReadAsArray() == 1

    gt = win.GetGeoTransform()
    rows, cols = z.shape
    cx = gt[0] + (np.arange(cols) + 0.5) * gt[1]
    cy = gt[3] + (np.arange(rows) + 0.5) * gt[5]
    dx = cx[None, :] - x
    dy = cy[:, None] - y
    dist = np.hypot(dx, dy)
    az = np.degrees(np.arctan2(dx, dy)) % 360.0
    daz = (az - heading + 180.0) % 360.0 - 180.0
    cam_z = float(z[rows // 2, cols // 2]) + EYE
    elev = np.degrees(np.arctan2(z - cam_z, np.maximum(dist, 1e-6)))

    in_view = (np.abs(daz) <= hfov / 2) & (np.abs(elev - pitch) <= vfov / 2)
    return visible & in_view, gt


def main():
    ds = make_dsm()
    c = SIZE // 2
    x = (c + 0.5) * CELL
    y = SIZE * CELL - (c + 0.5) * CELL

    # Typical phone: 26 mm equivalent, 4:3 landscape -> ~65 x 51 degrees
    hfov, vfov = 65.5, 51.0

    # Warm-up, then time several runs
    camera_footprint(ds, x, y, 0.0, -5.0, hfov, vfov)
    runs = 20
    t0 = time.perf_counter()
    for i in range(runs):
        mask, gt = camera_footprint(ds, x, y, (i * 17) % 360, -5.0, hfov, vfov)
    per = (time.perf_counter() - t0) / runs * 1000

    # Facing north (building ahead) vs south (open ground)
    north, gt = camera_footprint(ds, x, y, 0.0, -5.0, hfov, vfov)
    south, _ = camera_footprint(ds, x, y, 180.0, -5.0, hfov, vfov)

    # Rows (north of camera) more than 70 m away should be hidden behind the building
    rows = north.shape[0]
    behind = north[: rows // 2 - 70, rows // 2 - 5: rows // 2 + 5]
    print(f"window: {north.shape[1]} x {north.shape[0]} cells at {CELL} m")
    print(f"time per camera footprint: {per:.1f} ms  (target < 150 ms live)")
    print(f"batch estimate, 500 photos: {per * 500 / 1000:.1f} s  (target < 600 s)")
    print(f"visible cells facing north: {north.sum()}, facing south: {south.sum()}")
    print(f"cells directly behind building visible: {int(behind.sum())} (expect 0)")


if __name__ == "__main__":
    main()
