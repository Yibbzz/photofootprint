from qgis.core import QgsApplication
from qgis.PyQt.QtWidgets import QAction

from .provider import PhotoFootprintProvider

MENU = "&PhotoFootprint"


class PhotoFootprintPlugin:
    def __init__(self, iface):
        self.iface = iface
        self.provider = None
        self.actions = []

    def initProcessing(self):
        self.provider = PhotoFootprintProvider()
        QgsApplication.processingRegistry().addProvider(self.provider)

    def initGui(self):
        self.initProcessing()
        action = QAction("Import photos…", self.iface.mainWindow())
        action.triggered.connect(self.import_photos)
        self.iface.addPluginToMenu(MENU, action)
        self.actions.append(action)

    def unload(self):
        for action in self.actions:
            self.iface.removePluginMenu(MENU, action)
        self.actions = []
        if self.provider is not None:
            QgsApplication.processingRegistry().removeProvider(self.provider)
            self.provider = None

    def import_photos(self):
        import processing
        processing.execAlgorithmDialog("photofootprint:importphotos")
