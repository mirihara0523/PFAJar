import os,sys,tempfile
from pathlib import Path
from unittest.mock import patch
import numpy as np
import tifffile
os.environ['MASONJAR_IO_FAIRSHARE']='0'
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'py'))
import czi_extract as c
c.np,c.tiff=np,tifffile
for depth,role in [(8,'dapi'),(16,c.ROLE_SIGNAL_AXONS)]:
    for peaks in [(50,1000,200),(0,), (16383,)]:
        originals=[np.arange(80,dtype=np.uint16).reshape(8,10)*p//80 for p in peaks]
        reads=[]; previews=[]; times={}
        def read(_,scene,z,ch):
            reads.append(z)
            return originals[z].copy()
        def acc(k,v): times[k]=times.get(k,0)+v
        with tempfile.TemporaryDirectory() as d:
            out=Path(d)/'dotted.name.tif'
            with patch.object(c,'read_plane',read),patch.object(c,'_write_seam_grid_sidecar'),patch.object(c,'write_dapi_preview_pair',lambda b,s,p,v:previews.append(p.copy())),patch.object(c,'_perf_acc',acc):
                c.extract_z_stack(None,0,0,list(range(len(originals))),out,None,.05,'sample',Path(d),cfg={'bit_depth_by_role':{role:depth}},role_key=role)
            with tifffile.TiffFile(out) as tf:
                actual=np.stack([p.asarray() for p in tf.pages])
            stack=np.stack(originals)
            expected=stack if depth==16 else (stack.astype(np.float64)*255/max(1,int(stack.max()))).astype(np.uint8)
            assert np.array_equal(actual,expected)
            expected_reads = len(originals) * (2 if depth == 8 and len(originals) > 1 else 1)
            assert len(reads) == expected_reads
            if role=='dapi': assert np.array_equal(previews[0],c._preview_plane_from_stack(originals,[]))
            assert all(v>=0 for v in times.values())
print('Actual extract_z_stack: common scale, pages, preview, zero/single plane, and read passes passed')
