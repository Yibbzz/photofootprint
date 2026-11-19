from qgis.core import QgsApplication
from qgis.PyQt.QtWidgets import QAction

from .provider import PhotoFootprintProvider

MENU = "&PhotoFootprint"


class PhotoFootprintPlugin:
    def __init__(self, iface):
        self.iface = iface
        self.provider = None
        self.dock = None
        self.actions = []

    def initProcessing(self):
        self.provider = PhotoFootprintProvider()
        if not QgsApplication.processingRegistry().addProvider(self.provider):
            self.provider = None   # already registered (e.g. plugin reloaded); QGIS deleted ours

    def initGui(self):
        self.initProcessing()
        self._add_action("Import photos…", lambda: self._run("photofootprint:importphotos"))
        self._add_action("Generate footprints…", lambda: self._run("photofootprint:generatefootprints"))
        self._add_action("Find photos at points…", lambda: self._run("photofootprint:findphotos"))
        self.panel_action = self._add_action("Photo panel", self.toggle_panel, checkable=True)

    def _add_action(self, text, slot, checkable=False):
        action = QAction(text, self.iface.mainWindow())
        action.setCheckable(checkable)
        action.triggered.connect(slot)
        self.iface.addPluginToMenu(MENU, action)
        self.actions.append(action)
        return action

    def unload(self):
        for action in self.actions:
            self.iface.removePluginMenu(MENU, action)
        self.actions = []
        if self.dock is not None:
            self.dock.cleanup()
            self.iface.removeDockWidget(self.dock)
            self.dock.deleteLater()
            self.dock = None
        if self.provider is not None:
            QgsApplication.processingRegistry().removeProvider(self.provider)
            self.provider = None

    def _run(self, algorithm_id):
        import processing
        processing.execAlgorithmDialog(algorithm_id)

    def toggle_panel(self, checked=None):
        if self.dock is None:
            from . import compat
            from .ui.footprint_dock import FootprintDock
            self.dock = FootprintDock(self.iface, self.iface.mainWindow())
            self.dock.visibilityChanged.connect(self.panel_action.setChecked)
            self.iface.addDockWidget(compat.DOCK_RIGHT, self.dock)
        self.dock.setVisible(not self.dock.isVisible() if checked is None else bool(checked))
