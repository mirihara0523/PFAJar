"""Regression: changing the paint target restores the old region's raster."""
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "py"))

import numpy as np
from qtpy.QtGui import QColor, QPainter, QPixmap
from qtpy.QtWidgets import QApplication

from adjust import AnnotationViewer


class _PixmapItem:
    def __init__(self, pixmap):
        self._pixmap = pixmap

    def pixmap(self):
        return self._pixmap

    def setPixmap(self, pixmap):
        self._pixmap = pixmap


class _ViewerStub:
    def __init__(self):
        self.current_label = np.array([[1, 2]], dtype=np.uint32)
        # B,G,R,A cache: region 1 is RGB(30,20,10).
        self._anno_rgba = np.array([[[10, 20, 30, 255], [40, 50, 60, 255]]], dtype=np.uint8)
        pixmap = QPixmap(2, 1)
        pixmap.fill(QColor(218, 112, 214))
        self._anno_pixmap_item = _PixmapItem(pixmap)
        self.overlay_refreshes = 0

    def _anno_pixmap(self):
        return self._anno_pixmap_item.pixmap()

    def _set_anno_pixmap(self, pixmap):
        self._anno_pixmap_item.setPixmap(pixmap)

    def _display_overlay_pixmap(self):
        return self._anno_pixmap()

    def _set_img_overlay_layer(self, _pixmap):
        self.overlay_refreshes += 1


app = QApplication.instance() or QApplication([])
viewer = _ViewerStub()
AnnotationViewer._restore_region_highlight(viewer, 1)
pixel = viewer._anno_pixmap().toImage().pixelColor(0, 0)
assert (pixel.red(), pixel.green(), pixel.blue()) == (30, 20, 10)
assert viewer.overlay_refreshes == 1
print("Paint-target selection restores the prior region color and outline cache patch")
