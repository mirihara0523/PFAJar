"""Exercise actual preview and channel processing entry points on the same TIFF."""
from pathlib import Path
from types import SimpleNamespace
import contextlib,io,json,sys,tempfile
import numpy as np
import tifffile,cv2
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'py'))
import seam_correct as s
with tempfile.TemporaryDirectory() as temp:
    root=Path(temp); source=root/'input'; source.mkdir()
    meta=root/'meta'; (meta/'seamgrid').mkdir(parents=True)
    img=np.full((80,180),80,dtype=np.uint8); img[:,60:120]+=20; img[:,120:]+=10; img[40:]+=15
    path=source/'dotted.slice.tif'; tifffile.imwrite(path,img)
    grid={'scene_extent_px':[180,80],'vertical':{'boundaries_frac':[1/3,2/3]},'horizontal':{'boundaries_frac':[0.5]}}
    (meta/'seamgrid'/'dotted.slice.json').write_text(json.dumps(grid))
    for mode in ['known_geometry','grid_estimated']:
        log=io.StringIO()
        with contextlib.redirect_stdout(log):
            status=s.run_preview(SimpleNamespace(image=str(path),band=4,autotune=False,seam_mode=mode,height=80,x=0,y=0,w=180,preview_dir=str(meta)))
            written,stats=s._process_channel(source,root/mode,band=4,autotune=False,wanted=set(),meta_dir=meta,seam_mode=mode)
        assert status==0 and written==[path.name],log.getvalue()
        np.testing.assert_array_equal(cv2.imread(str(meta/'_seam_preview.png'),0),tifffile.imread(root/mode/path.name))
        if mode=='known_geometry':
            assert stats[0]['ramp_half_width']==0
            assert stats[0]['recorded_positions_vertical']==[60,120]
            assert stats[0]['recorded_positions_horizontal']==[40]
            assert 'paired_after' in log.getvalue() and 'valid_rows' in log.getvalue()
    # Exercise real entry points after one and then two successful rotations.
    history=[]
    for ops in [['rot90','flipX'],['rot90']]:
        for op in ops:
            img=np.rot90(img,k=-1) if op=='rot90' else np.fliplr(img)
        tifffile.imwrite(path,img)
        history.append({'kind':'file','ok':True,'file':'input/dotted.slice.tif','ops':ops})
        (meta/'geometry_history.jsonl').write_text('\n'.join(json.dumps(e) for e in history))
        with contextlib.redirect_stdout(io.StringIO()):
            status=s.run_preview(SimpleNamespace(image=str(path),band=4,autotune=False,seam_mode='known_geometry',height=img.shape[0],x=0,y=0,w=img.shape[1],preview_dir=str(meta)))
            written,stats=s._process_channel(source,root/'rotated',band=4,autotune=False,wanted=set(),meta_dir=meta,seam_mode='known_geometry')
        assert status==0 and written==[path.name]
        output=tifffile.imread(root/'rotated'/path.name)
        np.testing.assert_array_equal(cv2.imread(str(meta/'_seam_preview.png'),0),output)
        assert np.unique(output).size==1, 'Rotated horizontal and vertical tile steps must cancel'
    unsupported=s._seam_diagnostics(np.zeros((20,20),np.uint8),np.zeros((20,20),np.uint8),[10])[0]
    assert unsupported['paired_after'] is None and unsupported['valid_rows']==0
print('PASS Preview/Process pixel parity for both modes, default ramp, diagnostic support')
