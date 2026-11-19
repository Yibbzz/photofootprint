"""Processing algorithm: which photos show each point, best first."""

import numpy as np
from qgis.core import (
    QgsCoordinateTransform, QgsFeature, QgsFeatureSink, QgsField, QgsFields, QgsGeometry,
    QgsPointXY, QgsProcessingAlgorithm, QgsProcessingException,
    QgsProcessingParameterFeatureSink, QgsProcessingParameterFeatureSource,
    QgsProcessingParameterNumber, QgsProcessingParameterRasterLayer,
)

from .. import compat
from ..camera.attrs import camera_from_attributes
from ..footprint.algorithm import raster_path
from ..geo import to_grid, true_north_offset
from ..terrain.dem import Dem
from .rank import PhotoAt, find_photos

FIELDS = [
    ("point_fid", compat.INT), ("rank", compat.INT), ("photo_id", compat.STRING),
    ("path", compat.STRING), ("distance_m", compat.DOUBLE), ("gsd_cm", compat.DOUBLE),
    ("off_centre", compat.DOUBLE), ("u", compat.DOUBLE), ("v", compat.DOUBLE),
    ("score", compat.DOUBLE),
]


def photos_in_crs(source, dem_crs, context, feedback=None):
    """PhotoAt list (key = feature id) with positions and grid headings in the DEM CRS,
    plus attributes by key. `context` is anything with transformContext()."""
    to_dem = QgsCoordinateTransform(source.sourceCrs(), dem_crs, context.transformContext())
    photos, attrs_by_key = [], {}
    for feat in source.getFeatures():
        if feat.geometry().isEmpty():
            continue
        attrs = {f.name(): feat[f.name()] for f in source.fields()}
        try:
            setup = camera_from_attributes(attrs)
        except ValueError as e:
            if feedback:
                feedback.pushWarning(f"Ignoring photo {attrs.get('photo_id') or feat.id()}: {e}")
            continue
        pt = to_dem.transform(feat.geometry().asPoint())
        setup = to_grid(setup, true_north_offset(
            feat.geometry().asPoint(), source.sourceCrs(), dem_crs, context.transformContext()))
        photos.append(PhotoAt(feat.id(), setup, pt.x(), pt.y()))
        attrs_by_key[feat.id()] = attrs
    return photos, attrs_by_key


class FindPhotosAlgorithm(QgsProcessingAlgorithm):
    POINTS, PHOTOS, DEM, DTM = "POINTS", "PHOTOS", "DEM", "DTM"
    MAX_RANGE, TOP_N, OUTPUT = "MAX_RANGE", "TOP_N", "OUTPUT"

    def name(self):
        return "findphotos"

    def displayName(self):
        return "Find photos at points"

    def shortHelpString(self):
        return (
            "For each point, lists the photos that show it, best first. A photo shows "
            "a point when the point is inside its frame and nothing on the elevation "
            "model blocks the view.\n\nRanking: estimated detail at the point (cm per "
            "pixel), penalised the further the point is from the centre of the photo, "
            "where small compass errors matter least.\n\nUses each photo's current "
            "heading, pitch and field of view from the photo layer. Save the output "
            "as CSV for a spreadsheet."
        )

    def createInstance(self):
        return FindPhotosAlgorithm()

    def initAlgorithm(self, config=None):
        self.addParameter(QgsProcessingParameterFeatureSource(
            self.POINTS, "Points to look up", [compat.SOURCE_POINT]))
        self.addParameter(QgsProcessingParameterFeatureSource(
            self.PHOTOS, "Photo layer", [compat.SOURCE_POINT]))
        self.addParameter(QgsProcessingParameterRasterLayer(
            self.DEM, "Elevation model (DSM with buildings preferred)"))
        self.addParameter(QgsProcessingParameterRasterLayer(
            self.DTM, "Ground model (DTM) for camera height", optional=True))
        self.addParameter(QgsProcessingParameterNumber(
            self.MAX_RANGE, "Maximum range (m)", type=compat.NUMBER_DOUBLE,
            defaultValue=500.0, minValue=1.0))
        self.addParameter(QgsProcessingParameterNumber(
            self.TOP_N, "Photos per point (0 = all)", type=compat.NUMBER_INTEGER,
            defaultValue=3, minValue=0))
        self.addParameter(QgsProcessingParameterFeatureSink(
            self.OUTPUT, "Photos found", type=compat.SOURCE_POINT))

    def processAlgorithm(self, parameters, context, feedback):
        points = self.parameterAsSource(parameters, self.POINTS, context)
        photos_src = self.parameterAsSource(parameters, self.PHOTOS, context)
        dem_layer = self.parameterAsRasterLayer(parameters, self.DEM, context)
        dtm_layer = self.parameterAsRasterLayer(parameters, self.DTM, context)
        max_range = self.parameterAsDouble(parameters, self.MAX_RANGE, context)
        top_n = self.parameterAsInt(parameters, self.TOP_N, context)

        dem_crs = dem_layer.crs()
        if not dem_crs.isValid() or dem_crs.isGeographic():
            raise QgsProcessingException(
                "The elevation model must be in a projected CRS in metres (e.g. EPSG:27700).")
        dem = Dem(raster_path(dem_layer))
        dtm = Dem(raster_path(dtm_layer)) if dtm_layer is not None else None

        photos, attrs = photos_in_crs(photos_src, dem_crs, context, feedback)
        if not photos:
            raise QgsProcessingException("No usable photos in the photo layer.")
        px = np.array([p.x for p in photos])
        py = np.array([p.y for p in photos])

        fields = QgsFields()
        for name, typ in FIELDS:
            fields.append(QgsField(name, typ))
        sink, dest = self.parameterAsSink(parameters, self.OUTPUT, context, fields,
                                          compat.WKB_POINT, dem_crs)
        to_dem = QgsCoordinateTransform(points.sourceCrs(), dem_crs, context.transformContext())

        total = points.featureCount() or 1
        n_points = n_found = 0
        for i, feat in enumerate(points.getFeatures()):
            if feedback.isCanceled():
                break
            feedback.setProgress(100.0 * i / total)
            if feat.geometry().isEmpty():
                continue
            pt = to_dem.transform(feat.geometry().asPoint())
            near = np.hypot(px - pt.x(), py - pt.y()) <= max_range
            candidates = [p for p, keep in zip(photos, near) if keep]
            n_points += 1
            if not candidates:
                continue
            try:
                win = dem.window(pt.x(), pt.y(), max_range + 5 * dem.res)
            except ValueError:
                feedback.pushWarning(f"Point {feat.id()} is outside the elevation model.")
                continue
            matches = find_photos(win, pt.x(), pt.y(), candidates, max_range, dtm)
            if top_n:
                matches = matches[:top_n]
            n_found += bool(matches)
            for rank, m in enumerate(matches, 1):
                a = attrs[m.key]
                out = QgsFeature()
                out.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(pt.x(), pt.y())))
                out.setAttributes([feat.id(), rank, a.get("photo_id"), a.get("path"),
                                   round(m.distance_m, 1), round(m.gsd_cm, 2),
                                   round(m.off_centre, 3), round(m.u, 1), round(m.v, 1),
                                   round(m.score, 3)])
                sink.addFeature(out, QgsFeatureSink.FastInsert)

        feedback.pushInfo(f"{n_found} of {n_points} points are shown in at least one photo.")
        return {self.OUTPUT: dest, "POINTS": n_points, "FOUND": n_found}
