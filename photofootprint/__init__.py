"""PhotoFootprint QGIS plugin.

Keep this file free of QGIS imports at module level so the pure-Python
modules (camera/, importer/exif.py, importer/scan.py) can be tested without QGIS.
"""


def classFactory(iface):
    from .plugin import PhotoFootprintPlugin
    return PhotoFootprintPlugin(iface)
