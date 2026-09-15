import os,sys,tempfile
from pathlib import Path
import numpy as np
import tifffile as t
os.environ['MASONJAR_IO_FAIRSHARE']='0'
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'py'))
import czi_extract as c
import apply_geometry as g
c.np,c.tiff=np,t
failures=[]
def check(label,actual,expected):
    ok=np.array_equal(actual,expected)
    print(('PASS ' if ok else 'FAIL ')+label+f' shape={actual.shape} expected={expected.shape}')
    if not ok: failures.append(label)
with tempfile.TemporaryDirectory() as d:
    p=Path(d)/'stack.with.dots.tif'; m=Path(d)/'max.tif'
    stack=np.zeros((4,12,17),dtype=np.uint8)
    stack[0,1,2]=10
    stack[-1,8,13]=250
    c.write_pipeline_tiff_iter(p,iter(stack),8)
    with t.TiffFile(p) as tif:
        check('all stored TIFF pages',np.stack([page.asarray() for page in tif.pages]),stack)
    c.max_project_file(p,m)
    check('automatic import MAX includes final Z signal',t.imread(m),stack.max(axis=0))
    legacy=Path(d)/'legacy.tif'
    t.imwrite(legacy,stack,photometric='minisblack')
    c.max_project_file(legacy,m)
    check('legacy stack MAX',t.imread(m),stack.max(axis=0))
    check('Orient reader',g._read_tiff_array(p),stack)
    g.transform_file(p,g.compose_ops(90,False,False))
    check('Orient rotation preserves full stack',g._read_tiff_array(p),np.rot90(stack,-1,axes=(-2,-1)))
if failures: raise AssertionError('Downstream regressions: '+', '.join(failures))

