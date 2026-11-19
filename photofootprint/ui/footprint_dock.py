"""The PhotoFootprint panel.

- Select a photo on the map: it opens in the viewer and its footprint is drawn.
- Heading and pitch sliders redraw the footprint live; Save writes them to the photo.
- Click in the photo: a marker drops on the map where that pixel hits the ground.
- "Find photos" map tool: click the map, get the photos that show that spot,
  best first. Choosing one opens it with the spot marked.
"""

import math

from qgis.core import (QgsCoordinateTransform, QgsGeometry, QgsPointXY, QgsProject,
                       QgsVectorLayer, edit)
from qgis.gui import (QgsDockWidget, QgsMapLayerComboBox, QgsMapToolEmitPoint, QgsRubberBand,
                      QgsVertexMarker)
from qgis.PyQt.QtCore import QTimer
from qgis.PyQt.QtGui import QColor
from qgis.PyQt.QtWidgets import (QDoubleSpinBox, QFormLayout, QHBoxLayout, QLabel,
                                 QListWidget, QListWidgetItem, QPushButton, QSlider,
                                 QSplitter, QVBoxLayout, QWidget)

from .. import compat
from ..camera.attrs import CameraSetup, camera_from_attributes
from ..footprint.algorithm import raster_path
from ..footprint.compute import prepare, timed_footprint
from ..geo import to_grid, true_north_offset
from ..lookup.algorithm import photos_in_crs
from ..lookup.rank import find_photos, image_to_ground
from ..paths import resolve_photo_path
from ..terrain.dem import Dem
from .image_view import ImageView

DEBOUNCE_MS = 60
USER_ROLE = compat.enum_qt("ItemDataRole", "UserRole")


class _AngleControl(QWidget):
    """Slider + spin box kept in step. Calls `on_change` when the user changes either."""

    def __init__(self, minimum, maximum, on_change, wrap=False):
        super().__init__()
        self.slider = QSlider(compat.HORIZONTAL)
        self.slider.setRange(int(minimum * 10), int(maximum * 10))
        self.spin = QDoubleSpinBox()
        self.spin.setRange(minimum, maximum)
        self.spin.setDecimals(1)
        self.spin.setSuffix("°")
        self.spin.setWrapping(wrap)
        self.spin.setMinimumWidth(80)
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
        super().__init__("PhotoFootprint", parent)
        self.setObjectName("PhotoFootprintDock")
        self.iface = iface
        self.canvas = iface.mapCanvas()
        self._prep_key = None
        self._prep = None
        self._dem = None
        self._photo = None          # (layer, feature id, setup, geometry, attributes)
        self._watched = None
        self._pending_marker = None  # (feature id, u, v) to show once that photo opens
        self.last_footprint = None
        self.last_ground_hit = None  # (x, y, z, distance) in the DEM CRS
        self.last_matches = []

        # --- settings
        form = QFormLayout()
        self.photo_combo = QgsMapLayerComboBox()
        self.photo_combo.setFilters(compat.LAYER_FILTER_POINT)
        self.dem_combo = QgsMapLayerComboBox()
        self.dem_combo.setFilters(compat.LAYER_FILTER_RASTER)
        self.range_spin = QDoubleSpinBox()
        self.range_spin.setRange(10, 5000)
        self.range_spin.setValue(500)
        self.range_spin.setSuffix(" m")
        form.addRow("Photos", self.photo_combo)
        form.addRow("Elevation", self.dem_combo)
        form.addRow("Max range", self.range_spin)

        # --- photo viewer
        self.photo_label = QLabel("Select a photo on the map, or use Find photos.")
        self.photo_label.setWordWrap(True)
        self.prev_button = QPushButton("◀")
        self.next_button = QPushButton("▶")
        for b, step in ((self.prev_button, -1), (self.next_button, 1)):
            b.setFixedWidth(32)
            b.clicked.connect(lambda _=False, s=step: self.step_photo(s))
        nav = QHBoxLayout()
        nav.addWidget(self.prev_button)
        nav.addWidget(self.photo_label, 1)
        nav.addWidget(self.next_button)
        self.image = ImageView()
        self.image.setToolTip("Scroll to zoom, drag to pan, click to find that spot on the map.")
        self.image.clicked.connect(self.image_clicked)

        # --- angles
        angles = QFormLayout()
        self.heading = _AngleControl(0, 359.9, self.schedule_update, wrap=True)
        self.pitch = _AngleControl(-90, 90, self.schedule_update)
        angles.addRow("Heading", self.heading)
        angles.addRow("Pitch", self.pitch)
        self.status = QLabel("")
        self.status.setWordWrap(True)
        self.save_button = QPushButton("Save heading and pitch")
        self.save_button.setEnabled(False)
        self.save_button.clicked.connect(self.save)
        self.find_button = QPushButton("Find photos at a map spot")
        self.find_button.setCheckable(True)
        self.find_button.setToolTip("Then click the map. Uses each photo's saved heading.")
        self.find_button.toggled.connect(self._toggle_find_tool)
        buttons = QHBoxLayout()
        buttons.addWidget(self.find_button)
        buttons.addWidget(self.save_button)

        # --- lookup results
        self.results = QListWidget()
        self.results.setMinimumHeight(70)
        self.results.setVisible(False)
        self.results.itemClicked.connect(self._open_result)

        top = QWidget()
        tl = QVBoxLayout(top)
        tl.setContentsMargins(0, 0, 0, 0)
        tl.addLayout(form)
        tl.addLayout(nav)
        tl.addWidget(self.image, 1)
        bottom = QWidget()
        bl = QVBoxLayout(bottom)
        bl.setContentsMargins(0, 0, 0, 0)
        bl.addLayout(angles)
        bl.addLayout(buttons)
        bl.addWidget(self.status)
        bl.addWidget(self.results, 1)
        splitter = QSplitter(compat.enum_qt("Orientation", "Vertical"))
        splitter.addWidget(top)
        splitter.addWidget(bottom)
        bottom.setMinimumHeight(bottom.sizeHint().height())
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        splitter.setChildrenCollapsible(False)
        self.setWidget(splitter)

        # --- map graphics
        self.visible_band = QgsRubberBand(self.canvas, compat.GEOM_POLYGON)
        self.visible_band.setColor(QColor(230, 85, 13, 200))
        self.visible_band.setFillColor(QColor(230, 85, 13, 70))
        self.visible_band.setWidth(2)
        self.raw_band = QgsRubberBand(self.canvas, compat.GEOM_POLYGON)
        self.raw_band.setColor(QColor(120, 120, 120, 200))
        self.raw_band.setFillColor(QColor(0, 0, 0, 0))
        self.raw_band.setLineStyle(compat.DASH_LINE)
        self.raw_band.setWidth(1)
        self.hit_marker = self._marker(QColor(255, 220, 0), "ICON_X")
        self.lookup_marker = self._marker(QColor(49, 130, 189), "ICON_CROSS")

        self.find_tool = QgsMapToolEmitPoint(self.canvas)
        self.find_tool.canvasClicked.connect(self.find_photos_at)
        self.find_tool.deactivated.connect(lambda: self.find_button.setChecked(False))

        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.setInterval(DEBOUNCE_MS)
        self.timer.timeout.connect(self.update_footprint)

        self.photo_combo.layerChanged.connect(self._photo_layer_changed)
        self.dem_combo.layerChanged.connect(self._dem_changed)
        self.range_spin.valueChanged.connect(self.schedule_update)
        self._photo_layer_changed(self.photo_combo.currentLayer())
        self._dem_changed(self.dem_combo.currentLayer())

    def _marker(self, color, icon):
        m = QgsVertexMarker(self.canvas)
        m.setColor(color)
        m.setIconType(compat.enum(QgsVertexMarker, "IconType", icon))
        m.setIconSize(16)
        m.setPenWidth(3)
        m.setVisible(False)
        return m

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
        self.hit_marker.setVisible(False)
        feats = list(layer.getSelectedFeatures()) if layer is not None else []
        if not feats:
            self.photo_label.setText("Select a photo on the map, or use Find photos.")
            self.image.clear()
            self.clear_bands()
            return
        feat = feats[0]
        attrs = {f.name(): feat[f.name()] for f in layer.fields()}
        try:
            setup = camera_from_attributes(attrs)
        except ValueError as e:
            self.photo_label.setText(f"Can't use this photo: {e}")
            self.image.clear()
            self.clear_bands()
            return
        name = attrs.get("photo_id") or feat.id()
        note = "" if setup.heading is not None else " (no heading: drag the slider to set one)"
        self.heading.set_value(setup.heading if setup.heading is not None else 0.0)
        self.pitch.set_value(setup.pitch)
        self._photo = (layer, feat.id(), setup, feat.geometry(), attrs)
        self.save_button.setEnabled(True)

        path = resolve_photo_path(str(attrs.get("path") or ""), layer.source())
        problem = self.image.load(path, setup.camera.width)
        self.photo_label.setText(f"Photo {name}{note}" + (f" (can't display: {problem})" if problem else ""))
        if self._pending_marker and self._pending_marker[0] == feat.id():
            self.image.show_marker(*self._pending_marker[1:])
        self._pending_marker = None
        self.schedule_update()

    def step_photo(self, step):
        """Select the previous/next photo in time order (then photo_id)."""
        layer = self._watched
        if layer is None:
            return
        names = layer.fields().names()
        feats = list(layer.getFeatures())
        if not feats:
            return

        def key(f):
            dt = f["datetime"] if "datetime" in names else None
            return (str(dt) if dt not in (None, "") and str(dt) != "NULL" else "~",
                    str(f["photo_id"]) if "photo_id" in names else "", f.id())

        order = [f.id() for f in sorted(feats, key=key)]
        current = self._photo[1] if self._photo else None
        i = order.index(current) if current in order else -1
        layer.selectByIds([order[(i + step) % len(order)]])

    # ------------------------------------------------------------ footprint

    def schedule_update(self, *args):
        self.timer.start()

    def current_setup(self):
        """The selected photo's camera with the slider angles applied, heading on the
        DEM's grid (the slider shows the true bearing)."""
        layer, _, s, geom, _ = self._photo
        offset = 0.0
        if self._dem is not None:
            offset = true_north_offset(geom.asPoint(), layer.crs(), self._dem[0].crs(),
                                       QgsProject.instance().transformContext())
        return to_grid(CameraSetup(s.camera, self.heading.value(), self.pitch.value(), s.roll,
                                   s.height_agl, s.is_360), offset)

    def _prepared(self):
        dem_layer, dem = self._dem
        layer, fid, setup, geom, _ = self._photo
        max_range = self.range_spin.value()
        key = (layer.id(), fid, dem_layer.id(), max_range, setup.height_agl)
        if key != self._prep_key:
            tr = QgsCoordinateTransform(layer.crs(), dem_layer.crs(), QgsProject.instance())
            pt = tr.transform(geom.asPoint())
            self._prep = prepare(dem, pt.x(), pt.y(), setup.height_agl, max_range)
            self._prep_key = key
        return self._prep

    def update_footprint(self):
        if self._photo is None:
            return
        if self._dem is None:
            self.status.setText("Choose an elevation model.")
            self.clear_bands()
            return
        dem_layer, _ = self._dem
        s = self.current_setup()
        try:
            prep = self._prepared()
            fp, ms = timed_footprint(prep, s.camera, s.heading, s.pitch, s.roll)
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

    # ------------------------------------------------------------ image -> map

    def image_clicked(self, u, v):
        if self._photo is None or self._dem is None:
            return
        self.image.show_marker(u, v)
        dem_layer, _ = self._dem
        try:
            prep = self._prepared()
            hit = image_to_ground(prep.window, self.current_setup(), prep.x, prep.y, u, v,
                                  self.range_spin.value())
        except Exception as e:
            self.status.setText(f"Can't place that point: {e}")
            return
        if hit is None:
            self.hit_marker.setVisible(False)
            self.last_ground_hit = None
            self.status.setText("That pixel doesn't hit the ground within the max range (sky or too far).")
            return
        p, dist = hit
        self.last_ground_hit = (float(p[0]), float(p[1]), float(p[2]), float(dist))
        tr = QgsCoordinateTransform(dem_layer.crs(), self.canvas.mapSettings().destinationCrs(),
                                    QgsProject.instance())
        self.hit_marker.setCenter(tr.transform(QgsPointXY(p[0], p[1])))
        self.hit_marker.setVisible(True)
        self.status.setText(f"Clicked point: E {p[0]:.1f}, N {p[1]:.1f}, height {p[2]:.1f} m, "
                            f"{dist:.1f} m from the camera ({dem_layer.crs().authid()}).")

    # ------------------------------------------------------------ map -> photos

    def _toggle_find_tool(self, on):
        if on:
            self.canvas.setMapTool(self.find_tool)
        elif self.canvas.mapTool() is self.find_tool:
            self.canvas.unsetMapTool(self.find_tool)

    def find_photos_at(self, point, button=None):
        """Point is in the canvas CRS."""
        layer = self._watched
        if layer is None or self._dem is None:
            self.status.setText("Choose a photo layer and an elevation model first.")
            return []
        dem_layer, dem = self._dem
        canvas_crs = self.canvas.mapSettings().destinationCrs()
        self.lookup_marker.setCenter(point)
        self.lookup_marker.setVisible(True)
        tr = QgsCoordinateTransform(canvas_crs, dem_layer.crs(), QgsProject.instance())
        pt = tr.transform(point)
        max_range = self.range_spin.value()
        photos, attrs = photos_in_crs(layer, dem_layer.crs(), QgsProject.instance())  # grid headings
        photos = [p for p in photos if math.hypot(p.x - pt.x(), p.y - pt.y()) <= max_range]
        try:
            win = dem.window(pt.x(), pt.y(), max_range + 5 * dem.res)
            matches = find_photos(win, pt.x(), pt.y(), photos, max_range)
        except ValueError as e:
            self.status.setText(f"Can't look up that spot: {e}")
            return []

        self.last_matches = matches
        self.results.clear()
        self.results.setVisible(True)
        if not matches:
            self.results.addItem("No photo shows this spot.")
            self.status.setText(f"No photo within {max_range:g} m shows this spot.")
            return matches
        for rank, m in enumerate(matches, 1):
            name = attrs[m.key].get("photo_id") or m.key
            item = QListWidgetItem(
                f"{rank}. {name}: {m.distance_m:.0f} m away, {m.gsd_cm:.1f} cm/px"
                + ("" if m.off_centre < 0.6 else ", near the edge"))
            item.setData(USER_ROLE, (m.key, m.u, m.v))
            self.results.addItem(item)
        self.status.setText(f"{len(matches)} photo(s) show this spot. Opened the best one.")
        self._open_result(self.results.item(0))
        return matches

    def _open_result(self, item):
        data = item.data(USER_ROLE) if item is not None else None
        if not data or self._watched is None:
            return
        fid, u, v = data
        if self._photo is not None and self._photo[1] == fid:
            self.image.show_marker(u, v)
            return
        self._pending_marker = (fid, u, v)
        self._watched.selectByIds([fid])

    # ------------------------------------------------------------ save

    def save(self):
        if self._photo is None:
            return
        layer, fid, _, _, _ = self._photo
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
        self._toggle_find_tool(False)
        self._photo_layer_changed(None)
        for item in (self.visible_band, self.raw_band, self.hit_marker, self.lookup_marker):
            self.canvas.scene().removeItem(item)
