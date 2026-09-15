"""Owned image graphics item: edit pixels without replacing a full scene item."""
from qtpy.QtWidgets import QGraphicsItem
from qtpy.QtGui import QImage, QPixmap, QPainter, QColor
from qtpy.QtCore import QRectF


class EditableRaster(QGraphicsItem):
    def __init__(self, pixmap):
        super().__init__()
        self.image = QImage()
        self.setPixmap(pixmap)

    def boundingRect(self):
        return QRectF(0, 0, self.image.width(), self.image.height())

    def paint(self, painter, option, widget=None):
        rect = option.exposedRect.intersected(self.boundingRect())
        painter.drawImage(rect, self.image, rect)

    def setPixmap(self, pixmap):
        self.prepareGeometryChange()
        # Own detached pixels. No scene/pixmap reference can cause drag-time COW.
        self.image = pixmap.toImage().convertToFormat(QImage.Format.Format_ARGB32).copy()
        self.update()

    def pixmap(self):
        return QPixmap.fromImage(self.image)

    def paint_points(self, points):
        if not points:
            return
        painter = QPainter(self.image)
        painter.setPen(QColor(218, 112, 214))
        painter.drawPoints(points)
        painter.end()
        xs = [p.x() for p in points]
        ys = [p.y() for p in points]
        dirty = QRectF(min(xs), min(ys), max(xs)-min(xs)+1, max(ys)-min(ys)+1)
        self.update(dirty)

