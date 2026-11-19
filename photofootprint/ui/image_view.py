"""Zoomable photo view. Reports clicks in the camera model's pixel coordinates."""

from qgis.PyQt.QtCore import QPointF, pyqtSignal
from qgis.PyQt.QtGui import QColor, QImageReader, QPainterPath, QPen, QPixmap
from qgis.PyQt.QtWidgets import (QGraphicsPathItem, QGraphicsPixmapItem, QGraphicsScene,
                                 QGraphicsView)

from .. import compat

CLICK_TOLERANCE_PX = 4     # a press/release closer than this is a click, not a drag
ZOOM_STEP = 1.25


def _event_pos(event):
    return event.position().toPoint() if hasattr(event, "position") else event.pos()


class ImageView(QGraphicsView):
    clicked = pyqtSignal(float, float)      # u, v in camera-model pixels

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setScene(QGraphicsScene(self))
        self.setDragMode(compat.enum(QGraphicsView, "DragMode", "ScrollHandDrag"))
        self.setTransformationAnchor(compat.enum(QGraphicsView, "ViewportAnchor", "AnchorUnderMouse"))
        self.setMinimumHeight(180)
        self._pixmap_item = None
        self._marker = None
        self._scale = 1.0          # camera-model pixels per loaded image pixel
        self._press = None
        self._fitted = True

    # ------------------------------------------------------------ image

    def load(self, path, model_width):
        """Show the photo. Returns None on success, or a reason it can't be shown."""
        self.clear()
        reader = QImageReader(path)
        reader.setAutoTransform(True)          # apply EXIF orientation, like the importer does
        image = reader.read()
        if image.isNull():
            reason = reader.errorString()
            if path.lower().endswith((".heic", ".heif")):
                reason = "this QGIS has no HEIC image support (footprints and lookup still work)"
            return reason or "unreadable image"
        self._pixmap_item = QGraphicsPixmapItem(QPixmap.fromImage(image))
        self._pixmap_item.setTransformationMode(compat.enum_qt("TransformationMode", "SmoothTransformation"))
        self.scene().addItem(self._pixmap_item)
        self.scene().setSceneRect(self._pixmap_item.boundingRect())
        self._scale = float(model_width) / image.width() if model_width else 1.0
        self.fit()
        return None

    def clear(self):
        self.scene().clear()
        self._pixmap_item = None
        self._marker = None

    def has_image(self):
        return self._pixmap_item is not None

    def fit(self):
        if self._pixmap_item is not None:
            self.fitInView(self._pixmap_item, compat.enum_qt("AspectRatioMode", "KeepAspectRatio"))
            self._fitted = True

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._fitted:
            self.fit()

    def wheelEvent(self, event):
        if self._pixmap_item is None:
            return
        factor = ZOOM_STEP ** (event.angleDelta().y() / 120.0)
        self.scale(factor, factor)
        self._fitted = False

    # ------------------------------------------------------------ clicks

    def mousePressEvent(self, event):
        self._press = _event_pos(event)
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        super().mouseReleaseEvent(event)
        if self._press is None or self._pixmap_item is None:
            return
        pos = _event_pos(event)
        if (pos - self._press).manhattanLength() <= CLICK_TOLERANCE_PX:
            p = self.mapToScene(pos)
            rect = self._pixmap_item.boundingRect()
            if rect.contains(p):
                self.clicked.emit(p.x() * self._scale, p.y() * self._scale)
        self._press = None

    # ------------------------------------------------------------ marker

    def show_marker(self, u, v):
        """Crosshair at camera-model pixel (u, v); stays the same size when zooming."""
        if self._pixmap_item is None:
            return
        if self._marker is None:
            path = QPainterPath()
            for a, b in (((-14, 0), (-4, 0)), ((4, 0), (14, 0)), ((0, -14), (0, -4)), ((0, 4), (0, 14))):
                path.moveTo(*a)
                path.lineTo(*b)
            path.addEllipse(QPointF(0, 0), 9, 9)
            self._marker = QGraphicsPathItem(path)
            pen = QPen(QColor(255, 220, 0))
            pen.setWidth(2)
            pen.setCosmetic(True)
            self._marker.setPen(pen)
            self._marker.setFlag(compat.enum(QGraphicsPathItem, "GraphicsItemFlag", "ItemIgnoresTransformations"), True)
            self._marker.setZValue(10)
            self.scene().addItem(self._marker)
        self._marker.setPos(u / self._scale, v / self._scale)
        self._marker.setVisible(True)

    def marker_pos(self):
        """(u, v) of the crosshair in camera-model pixels, or None."""
        if self._marker is None or not self._marker.isVisible():
            return None
        return self._marker.pos().x() * self._scale, self._marker.pos().y() * self._scale

    def hide_marker(self):
        if self._marker is not None:
            self._marker.setVisible(False)
