import os,sys,tempfile,importlib
from pathlib import Path
from unittest.mock import patch
import numpy as np
import tifffile as t
os.environ['MASONJAR_IO_FAIRSHARE']='0'
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'py'))
import czi_extract as c
m=importlib.import_module('max')
c.np,c.tiff=np,t
with tempfile.TemporaryDirectory() as d:
    root=Path(d); p=root/'stack.dotted.tif'; dst=root/'out'; dst.mkdir()
    stack=np.zeros((4,12,17),dtype=np.uint8); stack[-1,8,13]=250
    c.write_pipeline_tiff_iter(p,iter(stack),8)
    assert m.process_file(str(p),str(dst))
    assert np.array_equal(t.imread(dst/p.name),stack.max(axis=0))
    captured=[]
    with patch.object(c,'original_scans_path',return_value=p),patch.object(c,'write_dapi_preview_pair',side_effect=lambda b,s,a,v:captured.append(a.copy())):
        assert c.repair_preview_from_zstack(root,{'role':'dapi'},'sample',.05)
    assert np.array_equal(captured[0],stack.max(axis=0))
print('PASS standalone MAX and preview repair retain final Z signal')
