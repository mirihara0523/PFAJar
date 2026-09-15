import os,sys,tempfile
from pathlib import Path
from unittest.mock import patch
import numpy as np
import tifffile as t
os.environ['MASONJAR_IO_FAIRSHARE']='0'
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'py'))
import czi_extract as c
c.np,c.tiff=np,t
with tempfile.TemporaryDirectory() as d:
    p=Path(d)/'original.tif'; original=b'preserve existing bytes'
    def broken():
        yield np.ones((8,9),dtype=np.uint8)
        raise OSError('synthetic CZI read failure')
    for mode in ['read','write','replace','empty']:
        p.write_bytes(original)
        try:
            if mode=='write':
                with patch.object(t.TiffWriter,'write',side_effect=OSError('synthetic disk full')):
                    c.write_pipeline_tiff_iter(p,iter([np.ones((8,9),dtype=np.uint8)]),8)
            elif mode=='replace':
                with patch.object(c.os,'replace',side_effect=PermissionError('locked target')):
                    c.write_pipeline_tiff_iter(p,iter([np.ones((8,9),dtype=np.uint8)]),8)
            else:
                c.write_pipeline_tiff_iter(p,broken() if mode=='read' else iter([]),8)
        except (OSError,ValueError): pass
        else: raise AssertionError('Expected failure '+mode)
        assert p.read_bytes()==original
        assert list(Path(d).iterdir())==[p]
    arr=np.arange(72,dtype=np.uint8).reshape(8,9)
    assert c.write_pipeline_tiff_iter(p,iter([arr]),8)==1
    assert np.array_equal(t.imread(p),arr)
    assert list(Path(d).iterdir())==[p]
print('PASS read/write/replace/empty failures preserve output, clean temp; retry commits')
