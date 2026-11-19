"""Processing algorithm: import a folder of photos into a photo point layer."""

import math
import os

from qgis.core import (
    QgsCoordinateReferenceSystem, QgsCoordinateTransform, QgsFeature, QgsFeatureSink,
    QgsField, QgsFields, QgsGeometry, QgsPointXY, QgsProcessingAlgorithm,
    QgsProcessingException, QgsProcessingOutputNumber, QgsProcessingParameterBoolean, QgsProcessingParameterCrs,
    QgsProcessingParameterFeatureSink, QgsProcessingParameterFile,
    QgsProcessingParameterNumber, QgsProcessingUtils,
)

from .. import compat
from ..camera.intrinsics import DEFAULT_FOCAL_35MM
from .scan import iter_photos, build_record

WGS84 = QgsCoordinateReferenceSystem("EPSG:4326")

# (name, type) in layer order. Matches the brief's data model.
PHOTO_FIELDS = [
    ("photo_id", compat.STRING), ("path", compat.STRING), ("type", compat.STRING),
    ("datetime", compat.STRING), ("x", compat.DOUBLE), ("y", compat.DOUBLE),
    ("z", compat.DOUBLE), ("height_agl", compat.DOUBLE),
    ("heading", compat.DOUBLE), ("heading_ref", compat.STRING), ("heading_source", compat.STRING),
    ("pitch", compat.DOUBLE), ("pitch_source", compat.STRING), ("roll", compat.DOUBLE),
    ("hfov", compat.DOUBLE), ("vfov", compat.DOUBLE), ("focal_px", compat.DOUBLE),
    ("focal_mm", compat.DOUBLE), ("focal_35mm", compat.DOUBLE), ("sensor_w_mm", compat.DOUBLE),
    ("fov_source", compat.STRING), ("width", compat.INT), ("height", compat.INT),
    ("gps_error_m", compat.DOUBLE), ("make", compat.STRING), ("model", compat.STRING),
    ("status", compat.STRING), ("notes", compat.STRING),
]


def photo_fields() -> QgsFields:
    fields = QgsFields()
    for name, typ in PHOTO_FIELDS:
        fields.append(QgsField(name, typ))
    return fields


def utm_crs(lon: float, lat: float) -> QgsCoordinateReferenceSystem:
    zone = min(int((lon + 180) // 6) + 1, 60)
    return QgsCoordinateReferenceSystem(f"EPSG:{(32600 if lat >= 0 else 32700) + zone}")


def stored_path(photo_path: str, output: str) -> str:
    """Path relative to the output file's folder when possible, else absolute."""
    out = output.split("|")[0]
    if out.startswith("ogr:") or not os.path.isabs(out):
        return photo_path
    try:
        return os.path.relpath(photo_path, os.path.dirname(out))
    except ValueError:   # different drive on Windows
        return photo_path


class ImportPhotosAlgorithm(QgsProcessingAlgorithm):
    FOLDER, RECURSIVE, CRS = "FOLDER", "RECURSIVE", "CRS"
    EYE_HEIGHT, DEFAULT_PITCH, DEFAULT_FOCAL = "EYE_HEIGHT", "DEFAULT_PITCH", "DEFAULT_FOCAL_35MM"
    OUTPUT = "OUTPUT"

    def name(self):
        return "importphotos"

    def displayName(self):
        return "Import photos"

    def shortHelpString(self):
        return (
            "Reads GPS position, compass heading and lens data from JPEG/HEIC photos "
            "(normal and 360) and writes one point per photo. Photos without a GPS "
            "position are skipped and listed in the log. Values that had to be assumed "
            "(pitch, focal length) are marked in the *_source fields so you can see "
            "and edit them.\n\nCRS: leave empty to use the project CRS if it is "
            "projected, otherwise the local UTM zone."
        )

    def createInstance(self):
        return ImportPhotosAlgorithm()

    def initAlgorithm(self, config=None):
        self.addParameter(QgsProcessingParameterFile(
            self.FOLDER, "Photo folder", behavior=compat.FOLDER))
        self.addParameter(QgsProcessingParameterBoolean(
            self.RECURSIVE, "Include subfolders", defaultValue=False))
        self.addParameter(QgsProcessingParameterCrs(
            self.CRS, "Projected CRS for the layer (metres)", optional=True))
        self.addParameter(QgsProcessingParameterNumber(
            self.EYE_HEIGHT, "Camera height above ground (m)", type=compat.NUMBER_DOUBLE,
            defaultValue=1.6, minValue=0.0))
        self.addParameter(QgsProcessingParameterNumber(
            self.DEFAULT_PITCH, "Pitch to assume when the photo has none (degrees, + is up)",
            type=compat.NUMBER_DOUBLE, defaultValue=0.0, minValue=-90.0, maxValue=90.0))
        self.addParameter(QgsProcessingParameterNumber(
            self.DEFAULT_FOCAL, "35 mm equivalent focal length to assume when missing",
            type=compat.NUMBER_DOUBLE, defaultValue=DEFAULT_FOCAL_35MM, minValue=1.0))
        self.addParameter(QgsProcessingParameterFeatureSink(
            self.OUTPUT, "Photos", type=compat.SOURCE_POINT))
        self.addOutput(QgsProcessingOutputNumber("IMPORTED", "Photos imported"))
        self.addOutput(QgsProcessingOutputNumber("SKIPPED", "Photos skipped"))

    def processAlgorithm(self, parameters, context, feedback):
        folder = self.parameterAsFile(parameters, self.FOLDER, context)
        if not folder or not os.path.isdir(folder):
            raise QgsProcessingException(f"Folder not found: {folder}")
        recursive = self.parameterAsBool(parameters, self.RECURSIVE, context)
        options = dict(
            eye_height=self.parameterAsDouble(parameters, self.EYE_HEIGHT, context),
            default_pitch=self.parameterAsDouble(parameters, self.DEFAULT_PITCH, context),
            default_focal_35mm=self.parameterAsDouble(parameters, self.DEFAULT_FOCAL, context),
        )

        paths = list(iter_photos(folder, recursive))
        if not paths:
            raise QgsProcessingException("No JPEG or HEIC photos found in the folder.")

        records, skipped = [], []
        for i, path in enumerate(paths):
            if feedback.isCanceled():
                break
            try:
                records.append(build_record(path, **options))
            except (ValueError, OSError) as e:
                skipped.append((path, str(e)))
            except Exception as e:   # one bad file must never stop the import
                skipped.append((path, f"unexpected error: {e!r}"))
            feedback.setProgress(80.0 * (i + 1) / len(paths))

        for path, reason in skipped:
            feedback.pushWarning(f"Skipped {os.path.basename(path)}: {reason}")
        if not records:
            raise QgsProcessingException("None of the photos could be imported (see log).")

        crs = self.parameterAsCrs(parameters, self.CRS, context)
        if not crs.isValid():
            project_crs = context.project().crs() if context.project() else None
            if project_crs and project_crs.isValid() and not project_crs.isGeographic():
                crs = project_crs
            else:
                crs = utm_crs(sum(r.lon for r in records) / len(records),
                              sum(r.lat for r in records) / len(records))
        if crs.isGeographic():
            raise QgsProcessingException("Choose a projected CRS in metres, not latitude/longitude.")
        feedback.pushInfo(f"Using CRS {crs.authid()} ({crs.description()})")
        transform = QgsCoordinateTransform(WGS84, crs, context.transformContext())

        sink, dest_id = self.parameterAsSink(
            parameters, self.OUTPUT, context, photo_fields(), compat.WKB_POINT, crs)
        if sink is None:
            raise QgsProcessingException("Could not create the output layer.")
        output = self.parameterAsOutputLayer(parameters, self.OUTPUT, context) or ""

        for i, r in enumerate(records):
            pt = transform.transform(QgsPointXY(r.lon, r.lat))
            values = {
                "photo_id": r.photo_id, "path": stored_path(r.path, output), "type": r.type,
                "datetime": r.datetime, "x": pt.x(), "y": pt.y(), "z": r.gps_alt,
                "height_agl": r.height_agl, "heading": r.heading, "heading_ref": r.heading_ref,
                "heading_source": r.heading_source, "pitch": r.pitch,
                "pitch_source": r.pitch_source, "roll": r.roll, "hfov": r.hfov, "vfov": r.vfov,
                "focal_px": r.focal_px, "focal_mm": r.focal_mm, "focal_35mm": r.focal_35mm,
                "sensor_w_mm": r.sensor_w_mm, "fov_source": r.fov_source, "width": r.width,
                "height": r.height, "gps_error_m": r.gps_error_m, "make": r.make,
                "model": r.model, "status": r.status, "notes": "; ".join(r.notes),
            }
            f = QgsFeature()
            f.setGeometry(QgsGeometry.fromPointXY(pt))
            f.setAttributes([values[name] for name, _ in PHOTO_FIELDS])
            sink.addFeature(f, QgsFeatureSink.FastInsert)
            feedback.setProgress(80.0 + 20.0 * (i + 1) / len(records))

        no_heading = sum(1 for r in records if r.heading is None)
        feedback.pushInfo(f"Imported {len(records)} photos, skipped {len(skipped)}.")
        if no_heading:
            feedback.pushWarning(f"{no_heading} photos have no compass heading (status = no_heading).")

        self._results = {self.OUTPUT: dest_id, "IMPORTED": len(records), "SKIPPED": len(skipped)}
        return self._results

    def postProcessAlgorithm(self, context, feedback):
        from ..styles import style_photo_layer
        # The dict returned here replaces the results from processAlgorithm.
        layer = QgsProcessingUtils.mapLayerFromString(self._results[self.OUTPUT], context)
        if layer is not None:
            style_photo_layer(layer)
        return self._results
