from pathlib import Path
import ast
source = (Path(__file__).resolve().parents[2] / 'py' / 'adjust.py').read_text(encoding='utf-8')
tree = ast.parse(source)
start = source.index('    def paint_deltas')
end = source.index('    def _poll_save_exit', start)
segment = source[start:end]
assert '_set_img_overlay_layer' in segment
assert 'img_pixmap.copy()' not in segment
print('PASS drag path updates separate overlay layer without DAPI copy')
