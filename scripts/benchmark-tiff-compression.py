import time,tempfile
from pathlib import Path
import numpy as np,tifffile
out=Path(tempfile.mkdtemp(prefix='masonjar-tiff-bench-'))
patterns={'random':np.random.default_rng(7).integers(0,256,size=(10,2048,2048),dtype=np.uint8),'smooth':np.tile(np.arange(2048,dtype=np.uint8),(10,2048,1)),'sparse':np.zeros((10,2048,2048),dtype=np.uint8)}
patterns['sparse'][:,::64,::64]=255
for name,arr in patterns.items():
 for codec in (None,'zlib'):
  p=out/f'{name}-{codec or "none"}.tif';t=time.perf_counter();tifffile.imwrite(p,arr,compression=codec,bigtiff=True);print(f'{name:7} {codec or "none":4}\t{time.perf_counter()-t:.3f}s\t{p.stat().st_size/1048576:.2f}MiB')
print('DIR',out)
