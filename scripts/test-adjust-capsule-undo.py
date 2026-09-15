"""Regression tests for Adjustment Viewer's vectorized brush Undo path."""
from pathlib import Path
import sys

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "py"))
from adjust import AnnotationViewer, QPoint  # noqa: E402


def bare_viewer(labels: np.ndarray):
    viewer = AnnotationViewer.__new__(AnnotationViewer)
    viewer.current_label = labels
    viewer.selected_region_id = 77
    viewer.brush_size = 12
    viewer.current_delta = 0
    viewer.deltas = []
    viewer.originals = []
    viewer._stroke_seen = None
    viewer.was_changed = False
    viewer.paint_deltas = lambda _points: None
    viewer.show_image_with_overlay = lambda: None
    return viewer


class FakePixmap:
    """Minimal QPixmap surface for testing DAPI display bounds."""

    def __init__(self, width: int, height: int):
        self._width = width
        self._height = height

    def isNull(self):
        return False

    def width(self):
        return self._width

    def height(self):
        return self._height


def main() -> int:
    before = np.arange(120 * 160, dtype=np.uint32).reshape(120, 160)
    viewer = bare_viewer(before.copy())
    viewer._paint_capsule_segment(QPoint(20, 25), QPoint(130, 90))
    stroke = viewer.deltas[0]
    assert stroke["kind"] == "capsule"
    assert stroke["chunks"]
    assert np.count_nonzero(viewer.current_label == 77) > 0
    viewer.current_delta = 1
    viewer.undo_last_delta()
    assert np.array_equal(viewer.current_label, before), "capsule Undo did not restore labels"

    # Non-brush set/dict history from parcellation remains compatible.
    viewer = bare_viewer(before.copy())
    viewer.current_label[4, 3] = 999
    viewer.deltas = [{(3, 4)}]
    viewer.originals = [{(3, 4): int(before[4, 3])}]
    viewer.current_delta = 1
    viewer.undo_last_delta()
    assert viewer.current_label[4, 3] == before[4, 3]

    # An aspect-ratio-preserved DAPI pixmap can be smaller than the label
    # canvas. The map-only margin is never a paintable area.
    viewer = bare_viewer(np.zeros((120, 160), dtype=np.uint32))
    viewer.img_pixmap = FakePixmap(120, 90)
    assert viewer._is_inside_dapi_image(QPoint(119, 89))
    assert not viewer._is_inside_dapi_image(QPoint(120, 89))
    assert not viewer._is_inside_dapi_image(QPoint(40, 90))
    assert not viewer._is_inside_dapi_image(QPoint(-1, 20))
    print("Adjustment capsule Undo and DAPI paint-bound checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
