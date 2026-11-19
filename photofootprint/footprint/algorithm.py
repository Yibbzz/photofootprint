"""Processing algorithm: footprints for every photo in a photo layer."""

import os
from datetime import datetime, timezone

from qgis.core import (
    QgsCoordinateReferenceSystem, QgsCoordinateTransform, QgsFeature, QgsFeatureSink,
    QgsField, QgsFields, QgsGeometry, QgsProcessingAlgorithm, QgsProcessingException,
    QgsProcessingParameterEnum, QgsProcessingParameterFeatureSink,
    QgsProcessingParameterFeatureSource, QgsProcessingParameterNumber,
    QgsProcessingParameterRasterLayer, QgsProcessingUtils,
)

from .. import compat
from ..camera.attrs import camera_from_attributes
from ..terrain.dem import Dem
from .compute import COARSE_DEM_M, footprint, prepare

VISIBLE_FIELDS = [
    ("photo_id", compat.STRING), ("dem_source", compat.STRING), ("dem_res_m", compat.DOUBLE),
    ("max_range_m", compat.DOUBLE), ("visible_pct", compat.DOUBLE), ("area_m2", compat.DOUBLE),
    ("capped", compat.INT), ("occlusion", compat.INT), ("ground_z", compat.DOUBLE),
    ("ground_source", compat.STRING), ("heading", compat.DOUBLE), ("pitch", compat.DOUBLE),
    ("created", compat.STRING),
]
RAW_FIELDS = [
    ("photo_id", compat.STRING), ("dem_source", compat.STRING), ("dem_res_m", compat.DOUBLE),
    ("max_range_m", compat.DOUBLE), ("capped", compat.INT), ("area_m2", compat.DOUBLE),
]
OCCLUSION_OPTIONS = [f"Auto (off for DEMs coarser than {COARSE_DEM_M:g} m)", "On", "Off"]


def _fields(spec):
    fields = QgsFields()
    for name, typ in spec:
        fields.append(QgsField(name, typ))
    return fields


def raster_path(layer) -> str:
    """GDAL-openable path of a QGIS raster layer."""
    src = layer.source()
    return src.split("|")[0] if os.path.exists(src.split("|")[0]) else src


class GenerateFootprintsAlgorithm(QgsProcessingAlgorithm):
    PHOTOS, DEM, DTM = "PHOTOS", "DEM", "DTM"
    MAX_RANGE, OCCLUSION = "MAX_RANGE", "OCCLUSION"
    VISIBLE, RAW = "VISIBLE", "RAW"

    def name(self):
        return "generatefootprints"

    def displayName(self):
        return "Generate footprints"

    def shortHelpString(self):
        return (
            "Works out the ground each photo shows. The raw footprint is everything "
            "inside the photo's frame on the terrain, out to the maximum range. The "
            "visible footprint removes what buildings, trees and terrain hide from "
            "the camera.\n\nUse a surface model (DSM) that includes buildings for "
            "occlusion. A ground model (DTM) is optional and only sets the height of "
            "the ground under each camera; without it, the lowest DSM cell within "
            "2 m is used.\n\nPhotos without a heading are skipped (360 photos don't need one)."
        )

    def createInstance(self):
        return GenerateFootprintsAlgorithm()

    def initAlgorithm(self, config=None):
        self.addParameter(QgsProcessingParameterFeatureSource(
            self.PHOTOS, "Photo layer", [compat.SOURCE_POINT]))
        self.addParameter(QgsProcessingParameterRasterLayer(
            self.DEM, "Elevation model (DSM with buildings preferred)"))
        self.addParameter(QgsProcessingParameterRasterLayer(
            self.DTM, "Ground model (DTM) for camera height", optional=True))
        self.addParameter(QgsProcessingParameterNumber(
            self.MAX_RANGE, "Maximum range (m)", type=compat.NUMBER_DOUBLE,
            defaultValue=500.0, minValue=1.0))
        self.addParameter(QgsProcessingParameterEnum(
            self.OCCLUSION, "Occlusion", options=OCCLUSION_OPTIONS, defaultValue=0))
        self.addParameter(QgsProcessingParameterFeatureSink(
            self.VISIBLE, "Visible footprints", type=compat.SOURCE_POLYGON))
        self.addParameter(QgsProcessingParameterFeatureSink(
            self.RAW, "Raw footprints (no occlusion)", type=compat.SOURCE_POLYGON, optional=True))

    def processAlgorithm(self, parameters, context, feedback):
        source = self.parameterAsSource(parameters, self.PHOTOS, context)
        dem_layer = self.parameterAsRasterLayer(parameters, self.DEM, context)
        dtm_layer = self.parameterAsRasterLayer(parameters, self.DTM, context)
        max_range = self.parameterAsDouble(parameters, self.MAX_RANGE, context)
        occ_choice = self.parameterAsEnum(parameters, self.OCCLUSION, context)
        if source is None or dem_layer is None:
            raise QgsProcessingException("A photo layer and an elevation model are required.")

        dem_crs = dem_layer.crs()
        if not dem_crs.isValid() or dem_crs.isGeographic():
            raise QgsProcessingException(
                "The elevation model must be in a projected CRS in metres (e.g. EPSG:27700).")
        dem = Dem(raster_path(dem_layer))
        dtm = None
        if dtm_layer is not None:
            if dtm_layer.crs() != dem_crs:
                raise QgsProcessingException("The DTM must be in the same CRS as the elevation model.")
            dtm = Dem(raster_path(dtm_layer))

        occlusion = {0: None, 1: True, 2: False}[occ_choice]
        if dem.res > COARSE_DEM_M:
            feedback.pushWarning(
                f"The elevation model is coarse ({dem.res:g} m cells). Footprints will be "
                "approximate" + ("; occlusion is off." if occlusion is None else "."))
        dem_source = f"{dem_layer.name()} ({os.path.basename(raster_path(dem_layer))})"

        to_dem = QgsCoordinateTransform(source.sourceCrs(), dem_crs, context.transformContext())
        vis_sink, vis_id = self.parameterAsSink(
            parameters, self.VISIBLE, context, _fields(VISIBLE_FIELDS), compat.WKB_MULTIPOLYGON, dem_crs)
        raw_sink, raw_id = self.parameterAsSink(
            parameters, self.RAW, context, _fields(RAW_FIELDS), compat.WKB_MULTIPOLYGON, dem_crs)
        if vis_sink is None:
            raise QgsProcessingException("Could not create the output layer.")

        created = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        total = source.featureCount() or 1
        done = skipped = 0
        for i, feat in enumerate(source.getFeatures()):
            if feedback.isCanceled():
                break
            feedback.setProgress(100.0 * i / total)
            attrs = {f.name(): feat[f.name()] for f in source.fields()}
            label = attrs.get("photo_id") or f"feature {feat.id()}"
            try:
                setup = camera_from_attributes(attrs)
                if setup.heading is None:
                    raise ValueError("no heading (set one in the attribute table or with the footprint panel)")
                if feat.geometry().isEmpty():
                    raise ValueError("no geometry")
                pt = to_dem.transform(feat.geometry().asPoint())
                prep = prepare(dem, pt.x(), pt.y(), setup.height_agl, max_range, dtm, occlusion)
                fp = footprint(prep, setup.camera, setup.heading, setup.pitch, setup.roll)
            except Exception as e:   # one bad photo must never stop the batch
                skipped += 1
                feedback.pushWarning(f"Skipped {label}: {e}")
                continue

            if fp.visible_wkt:
                f = QgsFeature()
                f.setGeometry(QgsGeometry.fromWkt(fp.visible_wkt))
                f.setAttributes([
                    attrs.get("photo_id"), dem_source, fp.dem_res_m, max_range,
                    round(fp.visible_pct, 1), round(fp.visible_area_m2, 1), int(fp.capped),
                    int(fp.occlusion), round(fp.ground_z, 2), fp.ground_source,
                    setup.heading, setup.pitch, created,
                ])
                vis_sink.addFeature(f, QgsFeatureSink.FastInsert)
            if raw_sink is not None and fp.raw_wkt:
                f = QgsFeature()
                f.setGeometry(QgsGeometry.fromWkt(fp.raw_wkt))
                f.setAttributes([attrs.get("photo_id"), dem_source, fp.dem_res_m, max_range,
                                 int(fp.capped), round(fp.raw_area_m2, 1)])
                raw_sink.addFeature(f, QgsFeatureSink.FastInsert)
            done += 1

        feedback.pushInfo(f"Footprints for {done} photos, skipped {skipped}.")
        self._results = {self.VISIBLE: vis_id, self.RAW: raw_id, "DONE": done, "SKIPPED": skipped}
        return self._results

    def postProcessAlgorithm(self, context, feedback):
        from ..styles import style_footprint_layer
        for key, raw in ((self.VISIBLE, False), (self.RAW, True)):
            if self._results.get(key):
                layer = QgsProcessingUtils.mapLayerFromString(self._results[key], context)
                if layer is not None:
                    style_footprint_layer(layer, raw=raw)
        return self._results
