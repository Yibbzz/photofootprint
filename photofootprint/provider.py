from qgis.core import QgsProcessingProvider

from .footprint.algorithm import GenerateFootprintsAlgorithm
from .importer.algorithm import ImportPhotosAlgorithm


class PhotoFootprintProvider(QgsProcessingProvider):
    def id(self):
        return "photofootprint"

    def name(self):
        return "PhotoFootprint"

    def longName(self):
        return "PhotoFootprint: site photo footprints"

    def loadAlgorithms(self):
        self.addAlgorithm(ImportPhotosAlgorithm())
        self.addAlgorithm(GenerateFootprintsAlgorithm())
