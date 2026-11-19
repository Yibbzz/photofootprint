"""Dock panel: live footprint for the selected photo, with heading and pitch sliders."""

from qgis.core import (QgsCoordinateTransform, QgsGeometry, QgsProject, QgsVectorLayer,
                       edit)
from qgis.gui import QgsDockWidget, QgsMapLayerComboBox, QgsRubberBand
from qgis.PyQt.QtCore import QTimer
from qgis.PyQt.QtGui import QColor
from qgis.PyQt.QtWidgets import (QDoubleSpinBox, QFormLayout, QHBoxLayout, QLabel,
                                 QPushButton, QSlider, QVBoxLayout, QWidget)

from .. import compat
from ..camera.attrs import camera_from_attributes
from ..footprint.algorithm import raster_path
from ..footprint.compute import prepare, timed_footprint
from ..terrain.dem import Dem

DEBOUNCE_MS = 60


class _AngleControl(QWidget):
    """Slider + spin box kept in step. Emits through `on_change`."""

    def __init__(self, minimum, maximum, on_change, wrap=False):
        super().__init__()
        self.slider = QSlider(compat.HORIZONTAL)
        self.slider.setRange(int(minimum * 10), int(maximum * 10))
        self.spin = QDoubleSpinBox()
        self.spin.setRange(minimum, maximum)
        self.spin.setDecimals(1)
        self.spin.setSuffix("°")
        self.spin.setWrapping(wrap)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.slider, 1)
        lay.addWidget(self.spin)
        self.slider.valueChanged.connect(lambda v: self._set(v / 10.0, from_slider=True))
        self.spin.valueChanged.connect(lambda v: self._set(v, from_slider=False))
        self._on_change = on_change

    def _set(self, value, from_slider):
        widget = self.spin if from_slider else self.slider
        widget.blockSignals(True)
        widget.setValue(value if from_slider else int(round(value * 10)))
        widget.blockSignals(False)
        self._on_change()

    def value(self):
        return self.spin.value()

    def set_value(self, value):
        for w, v in ((self.spin, value), (self.slider, int(round(value * 10)))):
            w.blockSignals(True)
            w.setValue(v)
            w.blockSignals(False)


class FootprintDock(QgsDockWidget):
    def __init__(self, iface, parent=None):
        super().__init__("Photo footprint", parent)
        self.setObjectName("PhotoFootprintDock")
        self.iface = iface
        self.canvas = iface.mapCanvas()
        self._prep_key = None
        self._prep = None
        self._dem = None
        self._photo = None          # (layer, feature id, setup, point in DEM CRS)
        self._watched = None
        self.last_footprint = None

        body = QWidget()
        form = QFormLayout()
        self.photo_combo = QgsMapLayerComboBox()
        self.photo_combo.setFilters(compat.LAYER_FILTER_POINT)
        self.dem_combo = QgsMapLayerComboBox()
        self.dem_combo.setFilters(compat.LAYER_FILTER_RASTER)
        self.range_spin = QDoubleSpinBox()
        self.range_spin.setRange(10, 5000)
        self.range_spin.setValue(500)
        self.range_spin.setSuffix(" m")
        self.heading = _AngleControl(0, 359.9, self.schedule_update, wrap=True)
        self.pitch = _AngleControl(-90, 90, self.schedule_update)
        form.addRow("Photos", self.photo_combo)
        form.addRow("Elevation", self.dem_combo)
        form.addRow("Max range", self.range_spin)
        form.addRow("Heading", self.heading)
        form.addRow("Pitch", self.pitch)

        self.photo_label = QLabel("Select a photo on the map.")
        self.photo_label.setWordWrap(True)
        self.status = QLabel("")
        self.status.setWordWrap(True)
        self.save_button = QPushButton("Save heading and pitch to photo")
        self.save_button.setEnabled(False)
        self.save_button.clicked.connect(self.save)

        lay = QVBoxLayout(body)
        lay.addWidget(self.photo_label)
        lay.addLayout(form)
        lay.addWidget(self.status)
        lay.addWidget(self.save_button)
        lay.addStretch(1)
        self.setWidget(body)

        self.visible_band = QgsRubberBand(self.canvas, compat.GEOM_POLYGON)
        self.visible_band.setColor(QColor(230, 85, 13, 200))
        self.visible_band.setFillColor(QColor(230, 85, 13, 70))
        self.visible_band.setWidth(2)
        self.raw_band = QgsRubberBand(self.canvas, compat.GEOM_POLYGON)
        self.raw_band.setColor(QColor(120, 120, 120, 200))
        self.raw_band.setFillColor(QColor(0, 0, 0, 0))
        self.raw_band.setLineStyle(compat.DASH_LINE)
        self.raw_band.setWidth(1)

        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.setInterval(DEBOUNCE_MS)
        self.timer.timeout.connect(self.update_footprint)

        self.photo_combo.layerChanged.connect(self._photo_layer_changed)
        self.dem_combo.layerChanged.connect(self._dem_changed)
        self.range_spin.valueChanged.connect(self.schedule_update)
        self._photo_layer_changed(self.photo_combo.currentLayer())
        self._dem_changed(self.dem_combo.currentLayer())

    # ------------------------------------------------------------ inputs

    def _photo_layer_changed(self, layer):
        if self._watched is not None:
            try:
                self._watched.selectionChanged.disconnect(self.load_selected)
            except (TypeError, RuntimeError):
                pass
        self._watched = layer if isinstance(layer, QgsVectorLayer) else None
        if self._watched is not None:
            self._watched.selectionChanged.connect(self.load_selected)
        self.load_selected()

    def _dem_changed(self, layer):
        self._dem = None
        self._prep_key = None
        if layer is not None:
            try:
                if layer.crs().isGeographic():
                    raise ValueError("elevation model must be in a projected CRS in metres")
                self._dem = (layer, Dem(raster_path(layer)))
            except Exception as e:
                self.status.setText(f"Can't use {layer.name()}: {e}")
        self.load_selected()

    def load_selected(self, *args):
        layer = self._watched
        self._photo = None
        self.save_button.setEnabled(False)
        feats = list(layer.getSelectedFeatures()) if layer is not None else []
        if not feats:
            self.photo_label.setText("Select a photo on the map.")
            self.clear_bands()
            return
        feat = feats[0]
        attrs = {f.name(): feat[f.name()] for f in layer.fields()}
        try:
            setup = camera_from_attributes(attrs)
        except ValueError as e:
            self.photo_label.setText(f"Can't use this photo: {e}")
            self.clear_bands()
            return
        heading_note = "" if setup.heading is not None else " (no heading: drag the slider to set one)"
        self.photo_label.setText(f"Photo {attrs.get('photo_id') or feat.id()}{heading_note}")
        self.heading.set_value(setup.heading if setup.heading is not None else 0.0)
        self.pitch.set_value(setup.pitch)
        self._photo = (layer, feat.id(), setup, feat.geometry())
        self.save_button.setEnabled(True)
        self.schedule_update()

    # ------------------------------------------------------------ footprint

    def schedule_update(self, *args):
        self.timer.start()

    def update_footprint(self):
        if self._photo is None:
            return
        if self._dem is None:
            self.status.setText("Choose an elevation model.")
            self.clear_bands()
            return
        dem_layer, dem = self._dem
        layer, fid, setup, geom = self._photo
        max_range = self.range_spin.value()
        key = (layer.id(), fid, dem_layer.id(), max_range, setup.height_agl)
        try:
            if key != self._prep_key:
                tr = QgsCoordinateTransform(layer.crs(), dem_layer.crs(), QgsProject.instance())
                pt = tr.transform(geom.asPoint())
                self._prep = prepare(dem, pt.x(), pt.y(), setup.height_agl, max_range)
                self._prep_key = key
            fp, ms = timed_footprint(self._prep, setup.camera, self.heading.value(),
                                     self.pitch.value(), setup.roll)
        except Exception as e:
            self.status.setText(f"No footprint: {e}")
            self.clear_bands()
            return

        self._set_band(self.visible_band, fp.visible_wkt, dem_layer.crs())
        self._set_band(self.raw_band, fp.raw_wkt, dem_layer.crs())
        occ = "occlusion on" if fp.occlusion else "occlusion off"
        capped = ", reaches max range" if fp.capped else ""
        self.status.setText(
            f"Visible {fp.visible_area_m2:,.0f} m² ({fp.visible_pct:.0f}% of frame){capped}. "
            f"DEM {fp.dem_res_m:g} m, {occ}. Updated in {ms:.0f} ms.")
        self.last_footprint = fp

    def _set_band(self, band, wkt, crs):
        if wkt:
            band.setToGeometry(QgsGeometry.fromWkt(wkt), crs)
        else:
            band.reset(compat.GEOM_POLYGON)

    def clear_bands(self):
        self.visible_band.reset(compat.GEOM_POLYGON)
        self.raw_band.reset(compat.GEOM_POLYGON)

    # ------------------------------------------------------------ save

    def save(self):
        if self._photo is None:
            return
        layer, fid, setup, _ = self._photo
        names = [f.name() for f in layer.fields()]
        values = {"heading": round(self.heading.value(), 1), "heading_source": "manual",
                  "pitch": round(self.pitch.value(), 1), "pitch_source": "manual"}
        missing = [n for n in values if n not in names]
        if missing:
            self.status.setText(f"Can't save: the layer has no {', '.join(missing)} field.")
            return
        was_editing = layer.isEditable()
        if was_editing:
            for name, value in values.items():
                layer.changeAttributeValue(fid, layer.fields().indexOf(name), value)
        else:
            with edit(layer):
                for name, value in values.items():
                    layer.changeAttributeValue(fid, layer.fields().indexOf(name), value)
        self.load_selected()
        note = "saved" if not was_editing else "changed; save the layer's edits to keep it"
        self.photo_label.setText(f"{self.photo_label.text()} ({note})")

    def cleanup(self):
        self.timer.stop()
        self._photo_layer_changed(None)
        for band in (self.visible_band, self.raw_band):
            self.canvas.scene().removeItem(band)
