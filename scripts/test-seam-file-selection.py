"""Regression tests for dotted filenames and automatic mode fallback."""
from pathlib import Path
from types import SimpleNamespace
import json, tempfile
import numpy as np
import tifffile
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'py'))
import seam_correct as s

with tempfile.TemporaryDirectory() as t:
    root=Path(t); source=root/'input'; source.mkdir(); meta=root/'meta'; (meta/'seamgrid').mkdir(parents=True)
    for name in ['202607.M554.M579.01.63.png','202607.M554.M579.01.64.png','202608.M554.M579.01.01.png']:
        tifffile.imwrite(source/name,np.full((12,16),80,np.uint8))
    sl=meta/'run_slice_list.json'; sl.write_text(json.dumps({'slice_ids':['202607']}))
    assert s._resolve_wanted_stems(str(sl)) == {'202607'}
    files=s._list_image_files_any(source)
    assert [p.name for p in files if s._slice_matches_wanted(s._slice_stem(p),{'202607'})] == ['202607.M554.M579.01.63.png','202607.M554.M579.01.64.png']
    written,stats=s._process_channel(source,root/'out',band=4,autotune=False,wanted={'202607'},meta_dir=meta,seam_mode='auto')
    assert len(written)==2 and all(x['mode']=='grid_estimated' for x in stats)
    # A sidecar makes auto prefer Known-geometry, while explicit grid mode wins.
    grid={'scene_extent_px':[16,12],'vertical':{'boundaries_frac':[.5]},'horizontal':{'boundaries_frac':[]}}
    (meta/'seamgrid'/'202607.M554.M579.01.63.json').write_text(json.dumps(grid))
    written,stats=s._process_channel(source,root/'known',band=4,autotune=False,wanted={'202607'},meta_dir=meta,seam_mode='auto')
    assert stats[0]['mode']=='known_geometry'
    written,stats=s._process_channel(source,root/'explicit-grid',band=4,autotune=False,wanted={'202607'},meta_dir=meta,seam_mode='grid_estimated')
    assert stats[0]['mode']=='grid_estimated'
print('PASS dotted filename selection, auto fallback, known preference, explicit grid override')
