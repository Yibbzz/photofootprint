"""Shared fixtures. QGIS is started once per test session."""

import os
import sys

import pytest


@pytest.fixture(scope="session")
def qgis_app():
    pytest.importorskip("qgis.core")
    from qgis.core import QgsApplication
    for p in ("/usr/share/qgis/python/plugins", "/usr/lib/qgis/python/plugins"):
        if os.path.isdir(p) and p not in sys.path:
            sys.path.append(p)
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QgsApplication([], True)
    app.initQgis()
    from processing.core.Processing import Processing
    Processing.initialize()
    from photofootprint.provider import PhotoFootprintProvider
    provider = PhotoFootprintProvider()
    QgsApplication.processingRegistry().addProvider(provider)
    yield app
    QgsApplication.processingRegistry().removeProvider(provider)
    # Destroy leftover widgets (map canvases, docks) while QGIS is still alive;
    # otherwise Python's shutdown can segfault after all tests have passed.
    import gc
    from qgis.PyQt.QtWidgets import QApplication
    for w in QApplication.topLevelWidgets():
        w.deleteLater()
    QApplication.processEvents()
    gc.collect()
    app.exitQgis()
