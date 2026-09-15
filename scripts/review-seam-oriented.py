from pathlib import Path
import json,sys,cv2
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'py'))
import seam_correct as s
root=Path(r'D:\Claude\masonjar-7.0.2-MC.1-improving-win32-x64\seam-review')
bundle=root/'geometry-evidence'; report={}
for suffix in ['63','64']:
    name='202607.M554.M579.01.'+suffix
    img=cv2.imread(str(root/(name+' original.png')),0)
    assert img is not None
    grid=s.load_seam_grid(bundle/'.masonjar',name,bundle/'data'/'counting'/'00_dapi'/(name+'.png'))
    assert grid and grid.get('applied_geometry_ops')
    out,info=s.correct_known_geometry(img,grid)
    assert cv2.imwrite(str(root/(name+' Known-geometry.png')),out)
    report[name]={'grid':grid,'result':info}
    print(name,grid['applied_geometry_ops'],info['recorded_positions_vertical'],info['seam_positions_vertical'],info['seam_positions_horizontal'])
(root/'oriented-seam-review.json').write_text(json.dumps(report,indent=2),encoding='utf-8')

