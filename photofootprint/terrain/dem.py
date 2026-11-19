"""Elevation model access with windowed reads. GDAL + NumPy only, no QGIS.

Never loads a whole raster: callers ask for a window around a camera.
"""

import math
from dataclasses import dataclass

import numpy as np
from osgeo import gdal

gdal.UseExceptions()


@dataclass
class Window:
    """A block of elevation cells. z has NaN where the DEM has no data."""
    z: np.ndarray
    gt: tuple          # GDAL geotransform of this window
    crs_wkt: str

    @property
    def res(self) -> float:
        return abs(self.gt[1])

    def cell_centres(self):
        """(xs, ys) 1-D arrays of column and row centre coordinates."""
        rows, cols = self.z.shape
        xs = self.gt[0] + (np.arange(cols) + 0.5) * self.gt[1]
        ys = self.gt[3] + (np.arange(rows) + 0.5) * self.gt[5]
        return xs, ys

    def cell_of(self, x, y):
        return (int(math.floor((y - self.gt[3]) / self.gt[5])),
                int(math.floor((x - self.gt[0]) / self.gt[1])))

    def contains(self, x, y) -> bool:
        r, c = self.cell_of(x, y)
        return 0 <= r < self.z.shape[0] and 0 <= c < self.z.shape[1]

    def lowest_near(self, x, y, radius: float):
        """Lowest elevation within `radius` of (x, y), ignoring no-data.

        Used as the ground height under a camera on a surface model, so a
        photo point that falls on a roof or tree cell still starts at ground level.
        """
        r, c = self.cell_of(x, y)
        n = max(int(math.ceil(radius / self.res)), 0)
        block = self.z[max(r - n, 0):r + n + 1, max(c - n, 0):c + n + 1]
        if block.size == 0 or np.all(np.isnan(block)):
            return None
        return float(np.nanmin(block))

    def value_at(self, x, y):
        if not self.contains(x, y):
            return None
        v = self.z[self.cell_of(x, y)]
        return None if np.isnan(v) else float(v)

    def to_mem(self, z=None):
        """In-memory GDAL dataset of this window (optionally with replaced values)."""
        z = self.z if z is None else z
        ds = gdal.GetDriverByName("MEM").Create("", z.shape[1], z.shape[0], 1, gdal.GDT_Float32)
        ds.SetGeoTransform(self.gt)
        ds.SetProjection(self.crs_wkt)
        band = ds.GetRasterBand(1)
        band.SetNoDataValue(-9999.0)
        band.WriteArray(np.where(np.isnan(z), -9999.0, z).astype(np.float32))
        return ds


class Dem:
    def __init__(self, path_or_ds, band: int = 1):
        self.ds = gdal.Open(path_or_ds) if isinstance(path_or_ds, str) else path_or_ds
        if self.ds is None:
            raise ValueError(f"cannot open elevation raster {path_or_ds}")
        self.band = self.ds.GetRasterBand(band)
        self.gt = self.ds.GetGeoTransform()
        if self.gt[2] or self.gt[4]:
            raise ValueError("rotated rasters are not supported")
        if abs(abs(self.gt[1]) - abs(self.gt[5])) > 1e-6 * abs(self.gt[1]):
            raise ValueError("elevation raster cells must be square")
        self.crs_wkt = self.ds.GetProjection()
        self.nodata = self.band.GetNoDataValue()

    @property
    def res(self) -> float:
        return abs(self.gt[1])

    def window(self, x, y, radius) -> Window:
        """Cells within a square of half-width `radius` around (x, y), clipped to the raster."""
        gt = self.gt
        col = int(math.floor((x - gt[0]) / gt[1]))
        row = int(math.floor((y - gt[3]) / gt[5]))
        n = int(math.ceil(radius / self.res)) + 1
        c0, r0 = max(col - n, 0), max(row - n, 0)
        c1 = min(col + n + 1, self.ds.RasterXSize)
        r1 = min(row + n + 1, self.ds.RasterYSize)
        if c1 <= c0 or r1 <= r0:
            raise ValueError("camera is outside the elevation raster")
        z = self.band.ReadAsArray(c0, r0, c1 - c0, r1 - r0).astype(np.float64)
        if self.nodata is not None:
            z[z == self.nodata] = np.nan
        wgt = (gt[0] + c0 * gt[1], gt[1], 0.0, gt[3] + r0 * gt[5], 0.0, gt[5])
        return Window(z, wgt, self.crs_wkt)
