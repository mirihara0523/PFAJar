from pathlib import Path
source=(Path(__file__).resolve().parents[2]/'py'/'adjust.py').read_text(encoding='utf-8')
segment=source[source.index('    def paint_deltas'):source.index('    def _poll_save_exit', source.index('    def paint_deltas'))]
assert 'new_annos = base.copy()' not in segment
assert 'self._anno_pixmap_item.paint_points(points)' in segment
assert 'self._set_anno_pixmap(' not in segment
print('PASS drag uses persistent raster; pixel/allocation coverage: scripts/test-adjust-raster.py')
