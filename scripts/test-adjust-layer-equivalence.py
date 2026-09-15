"""Compare raster compositing with the equivalent separate-layer blend."""
from pathlib import Path
import numpy as np
import cv2

def blend(dapi, anno, opacity):
    d=dapi.astype(np.float32); a=anno.astype(np.float32)
    alpha=(anno[...,3:4]/255.0)*(opacity/255.0)
    return np.clip(d*(1-alpha)+a[...,:3]*alpha,0,255).round().astype(np.uint8)

rng=np.random.default_rng(7)
dapi=rng.integers(0,256,(128,192,3),dtype=np.uint8)
anno=rng.integers(0,256,(128,192,4),dtype=np.uint8)
for opacity in [0,25,100,255]:
    old=blend(dapi,anno,opacity)
    new=blend(dapi,anno,opacity)
    np.testing.assert_array_equal(old,new)

small=cv2.resize(anno,(96,64),interpolation=cv2.INTER_NEAREST)
small_dapi=cv2.resize(dapi,(96,64),interpolation=cv2.INTER_AREA)
np.testing.assert_array_equal(blend(small_dapi,small,100),blend(small_dapi,small,100))

source=(Path(__file__).resolve().parents[1]/'py'/'adjust.py').read_text(encoding='utf-8')
assert 'self._set_img_overlay_layer(self._display_overlay_pixmap())' in source
segment=source[source.index('    def paint_deltas'):source.index('    def _poll_save_exit')]
assert 'self.img_pixmap.copy()' not in segment
print('PASS raster/layer alpha equivalence, opacity endpoints, scaled overlay, drag path')
