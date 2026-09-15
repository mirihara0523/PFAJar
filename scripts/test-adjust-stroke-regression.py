"""Execute actual viewer stroke methods without loading the full GUI dependency tree."""
import ast
from pathlib import Path
from functools import lru_cache
import numpy as np

tree=ast.parse((Path(__file__).resolve().parents[1]/'py/adjust.py').read_text(encoding='utf-8-sig'))
names={'points_in_circle','_circle_offsets','_paint_stroke','undo_last_delta'}
methods=[n for cls in tree.body if isinstance(cls,ast.ClassDef) for n in cls.body
         if isinstance(n,ast.FunctionDef) and n.name in names]
assert len(methods)==len(names)
cls=ast.ClassDef(name='Viewer',bases=[],keywords=[],body=methods,decorator_list=[])
ns={'lru_cache':lru_cache,'QPoint':lambda x,y:(x,y)}
exec(compile(ast.fix_missing_locations(ast.Module(body=[cls],type_ignores=[])),'actual-methods','exec'),ns)
for radius in (1,35,80):
    v=ns['Viewer'](); v.brush_size=radius; v.selected_region_id=99
    original=np.arange(120*160,dtype=np.uint32).reshape(120,160)
    v.current_label=original.copy(); v.deltas=[]; v.originals=[]; v.current_delta=0
    v._stroke_bbox=None; v.paint_deltas=lambda points:None; v.show_image_with_overlay=lambda:None
    centers=[(0,0),(80,60),(85,65),(159,119),(80,60)]
    expected=original.copy(); expected_points=set()
    for cx,cy in centers:
        for x in range(cx-radius,cx+radius+1):
            for y in range(cy-radius,cy+radius+1):
                if 0<=x<160 and 0<=y<120 and (x-cx)**2+(y-cy)**2<=radius**2:
                    expected[y,x]=99; expected_points.add((x,y))
    for center in centers:
        v._paint_stroke([center])
    assert np.array_equal(expected,v.current_label)
    assert v.deltas[0]==expected_points
    assert all(value==original[y,x] for (x,y),value in v.originals[0].items())
    v.current_delta=1; v.undo_last_delta()
    assert np.array_equal(original,v.current_label)
print('PASS actual stroke methods: radii 1/35/80, overlap, clipping, label equality, undo')
