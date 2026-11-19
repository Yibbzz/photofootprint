"""True north vs grid north.

Photo headings are compass bearings from true north. A projected CRS's grid
north differs from true north by the grid convergence: about 1.5 degrees in
London on the British National Grid, more near the edges of UTM zones.
Everything that turns a heading into directions on the map adds this offset.
The photo layer and the sliders keep showing true bearings.
"""

import dataclasses
import math

from qgis.core import QgsCoordinateReferenceSystem, QgsCoordinateTransform, QgsPointXY

WGS84 = QgsCoordinateReferenceSystem("EPSG:4326")


def true_north_offset(point, point_crs, grid_crs, transform_context) -> float:
    """Grid azimuth of true north at `point`, in degrees (add it to a true bearing)."""
    ll = QgsCoordinateTransform(point_crs, WGS84, transform_context).transform(QgsPointXY(point))
    to_grid = QgsCoordinateTransform(WGS84, grid_crs, transform_context)
    a = to_grid.transform(ll)
    b = to_grid.transform(QgsPointXY(ll.x(), min(ll.y() + 1e-4, 89.9999)))
    return math.degrees(math.atan2(b.x() - a.x(), b.y() - a.y()))


def to_grid(setup, offset_deg):
    """CameraSetup with its true heading turned into a grid heading."""
    if setup.heading is None:
        return setup
    return dataclasses.replace(setup, heading=(setup.heading + offset_deg) % 360.0)
