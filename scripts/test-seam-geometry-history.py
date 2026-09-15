from pathlib import Path
import sys,json,tempfile
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'py'))
import seam_correct as s
grid={'scene_extent_px':[200,100],'vertical':{'boundaries_frac':[0.2,0.6]},'horizontal':{'boundaries_frac':[0.3]}}
g=s._orient_seam_grid(grid,['rot90','flipX'])
assert g['scene_extent_px']==[100,200]
np.testing.assert_allclose(g['vertical']['boundaries_frac'],[0.3])
np.testing.assert_allclose(g['horizontal']['boundaries_frac'],[0.2,0.6])
for ops in [['rot90']*4,['flipX']*2,['flipY']*2]:
    g=s._orient_seam_grid(grid,ops)
    for axis in ['vertical','horizontal']:
        np.testing.assert_allclose(g[axis]['boundaries_frac'],grid[axis]['boundaries_frac'])
with tempfile.TemporaryDirectory() as t:
    root=Path(t); meta=root/'.masonjar'; (meta/'seamgrid').mkdir(parents=True)
    side=meta/'seamgrid'/'a.json'; side.write_text(json.dumps(grid))
    original=side.read_bytes()
    # No history file: preserve geometry and output exactly.
    loaded=s.load_seam_grid(meta,'a',root/'data'/'a.png')
    assert loaded==grid
    img=np.full((100,200),80,np.uint8); img[:,40:]+=20; img[:,120:]-=10; img[30:]+=15
    expected,_=s.correct_known_geometry(img,grid,refine=False)
    actual,_=s.correct_known_geometry(img,loaded,refine=False)
    np.testing.assert_array_equal(expected,actual)
    events=[{'kind':'file','ok':True,'file':'data/a.png','ops':['rot90','flipX']},
            {'kind':'file','ok':True,'file':'other/a.png','ops':['flipY']},
            {'kind':'file','ok':False,'file':'data/a.png','ops':['flipY']}]
    (meta/'geometry_history.jsonl').write_text('\n'.join(json.dumps(e) for e in events))
    a=s.load_seam_grid(meta,'a',root/'data'/'a.png')
    b=s.load_seam_grid(meta,'a',root/'data'/'a.png')
    assert a==b and side.read_bytes()==original
    assert a['applied_geometry_ops']==['rot90','flipX']
    assert s.load_seam_grid(meta,'a',root/'untouched'/'a.png')==grid
    # A second successful operation must compose with the first in log order.
    events.append({'kind':'file','ok':True,'file':'data/a.png','ops':['rot90']})
    (meta/'geometry_history.jsonl').write_text('\n'.join(json.dumps(e) for e in events))
    repeated=s.load_seam_grid(meta,'a',root/'data'/'a.png')
    assert repeated==s._orient_seam_grid(grid,['rot90','flipX','rot90'])
    other=s.load_seam_grid(meta,'a',root/'other'/'a.png')
    assert other==s._orient_seam_grid(grid,['flipY'])
    assert side.read_bytes()==original
print('PASS rotations, flips, exact-file history, failed records, repeat-load and sidecar preservation')
