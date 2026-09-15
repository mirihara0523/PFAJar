"""Real offscreen Qt pixel regression; timing is diagnostic, not a threshold."""
import os
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
import sys
from pathlib import Path
from time import perf_counter
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'py'))
from adjust_raster import EditableRaster
from qtpy.QtWidgets import QApplication, QGraphicsScene
from qtpy.QtGui import QPixmap, QPainter, QColor, QImage
from qtpy.QtCore import QPoint, QRectF

app = QApplication([])
base = QPixmap(2048, 1536)
base.fill(QColor(31, 62, 93))
item = EditableRaster(base)
scene = QGraphicsScene()
scene.addItem(item)
scene.setSceneRect(item.boundingRect())
legacy = QPixmap(base)
batches = [[QPoint(cx+x, 400+y) for x in range(-35,36) for y in range(-35,36)
            if x*x+y*y <= 35*35] for cx in range(0, 2000, 50)]
start = perf_counter()
for points in batches:
    frame = QPixmap(legacy)
    painter = QPainter(frame)
    painter.setPen(QColor(218,112,214))
    painter.drawPoints(points)
    painter.end()
    legacy = frame
old_time = perf_counter()-start
start = perf_counter()
address = int(item.image.constBits())
for points in batches:
    item.paint_points(points)
    assert int(item.image.constBits()) == address, 'Unexpected image reallocation'
new_time = perf_counter()-start
assert item.pixmap().toImage() == legacy.toImage(), 'Raster differs from legacy painter'
for opacity in (0, 0.4, 1):
    item.setOpacity(opacity)
    actual = QImage(2048,1536,QImage.Format.Format_ARGB32)
    expected = QImage(actual.size(),actual.format())
    actual.fill(QColor(20,30,40)); expected.fill(QColor(20,30,40))
    p=QPainter(actual); scene.render(p); p.end()
    p=QPainter(expected); p.setOpacity(opacity); p.drawPixmap(0,0,legacy); p.end()
    assert actual == expected, f'Layer blend differs at {opacity}'
item.setPixmap(base)
assert item.pixmap().toImage() == base.toImage(), 'Full refresh failed'
print(f'PASS pixels, clipping, opacity, stable image allocation, full refresh; old={old_time:.4f}s dirty={new_time:.4f}s')
