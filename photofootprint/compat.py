"""Names that differ between QGIS 3.34 (Qt5) and newer QGIS / QGIS 4 (Qt6)."""

from qgis.core import (Qgis, QgsProcessing, QgsProcessingParameterFile,
                       QgsProcessingParameterNumber)

NEW_API = Qgis.QGIS_VERSION_INT >= 33800

if NEW_API:
    from qgis.PyQt.QtCore import QMetaType
    STRING = QMetaType.Type.QString
    DOUBLE = QMetaType.Type.Double
    INT = QMetaType.Type.Int
else:
    from qgis.PyQt.QtCore import QVariant
    STRING = QVariant.String
    DOUBLE = QVariant.Double
    INT = QVariant.Int

FOLDER = getattr(getattr(Qgis, "ProcessingFileParameterBehavior", None), "Folder",
                 getattr(QgsProcessingParameterFile, "Folder", 1))
NUMBER_DOUBLE = getattr(getattr(Qgis, "ProcessingNumberParameterType", None), "Double",
                        getattr(QgsProcessingParameterNumber, "Double", 1))
SOURCE_POINT = getattr(getattr(Qgis, "ProcessingSourceType", None), "VectorPoint",
                       getattr(QgsProcessing, "TypeVectorPoint", 0))
SOURCE_POLYGON = getattr(getattr(Qgis, "ProcessingSourceType", None), "VectorPolygon",
                         getattr(QgsProcessing, "TypeVectorPolygon", 2))
WKB_POINT = Qgis.WkbType.Point
WKB_MULTIPOLYGON = Qgis.WkbType.MultiPolygon
GEOM_POLYGON = Qgis.GeometryType.Polygon


def symbol_property(name):
    """QgsSymbolLayer.Property<Name> (3.34) or QgsSymbolLayer.Property.<Name> (newer)."""
    from qgis.core import QgsSymbolLayer
    scoped = getattr(QgsSymbolLayer, "Property", None)
    if scoped is not None and hasattr(scoped, name):
        return getattr(scoped, name)
    return getattr(QgsSymbolLayer, "Property" + name)


# GUI enums: scoped in Qt6 / newer QGIS, flat in Qt5 / QGIS 3.34
from qgis.core import QgsMapLayerProxyModel  # noqa: E402
from qgis.PyQt.QtCore import Qt  # noqa: E402

_filters = getattr(Qgis, "LayerFilter", None)
LAYER_FILTER_POINT = getattr(_filters, "PointLayer", None) or QgsMapLayerProxyModel.PointLayer
LAYER_FILTER_RASTER = getattr(_filters, "RasterLayer", None) or QgsMapLayerProxyModel.RasterLayer


def _qt(enum, name):
    scoped = getattr(Qt, enum, None)
    return getattr(scoped, name) if scoped is not None and hasattr(scoped, name) else getattr(Qt, name)


HORIZONTAL = _qt("Orientation", "Horizontal")
DASH_LINE = _qt("PenStyle", "DashLine")
DOCK_RIGHT = _qt("DockWidgetArea", "RightDockWidgetArea")
