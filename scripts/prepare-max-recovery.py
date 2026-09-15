"""Prepare verified MAX candidates; never replace project outputs."""
import argparse,json,os,sys
from pathlib import Path
os.environ['MASONJAR_IO_FAIRSHARE']='0'
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'py'))
import numpy as np
import tifffile as t
import czi_extract as c
c.np,c.tiff=np,t

def prepare(source,dest):
    if dest.exists(): raise FileExistsError(dest)
    dest.parent.mkdir(parents=True,exist_ok=True)
    with t.TiffFile(source) as tif:
        depth = 8 if tif.pages[0].dtype == np.uint8 else 16
    c.max_project_file(source,dest,bit_depth=depth)
    actual=t.imread(dest)
    # Independently reduce every stored Z plane, including separate series.
    expected=None
    with t.TiffFile(source) as tif:
        for page in tif.pages:
            arr=page.asarray()
            for plane in ([arr] if arr.ndim==2 else arr):
                expected=plane.copy() if expected is None else np.maximum(expected,plane)
    expected=c.coerce_stack_depth(expected,depth)
    if not np.array_equal(actual,expected): raise ValueError('Pixel mismatch: '+str(source))
    return {'source':str(source),'candidate':str(dest),'verified':True}

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source',type=Path,help='One original_scans channel directory')
    parser.add_argument('output',type=Path,help='New candidate directory; must not exist')
    args=parser.parse_args()
    if args.output.exists(): parser.error('Output directory must not exist')
    files=sorted(args.source.glob('*.tif'))+sorted(args.source.glob('*.tiff'))
    if not files: parser.error('No TIFF inputs')
    if len({p.stem.lower() for p in files})!=len(files): parser.error('Duplicate output stems')
    args.output.mkdir(parents=True)
    report=[]
    for p in files: report.append(prepare(p,args.output/(p.stem+'.tif')))
    (args.output/'recovery-report.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(f'{len(report)} verified candidates; original outputs unchanged')
