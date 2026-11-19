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
WKB_POINT = Qgis.WkbType.Point


def symbol_property(name):
    """QgsSymbolLayer.Property<Name> (3.34) or QgsSymbolLayer.Property.<Name> (newer)."""
    from qgis.core import QgsSymbolLayer
    scoped = getattr(QgsSymbolLayer, "Property", None)
    if scoped is not None and hasattr(scoped, name):
        return getattr(scoped, name)
    return getattr(QgsSymbolLayer, "Property" + name)
