"""Controlled first-slice comparisons; preserve original and mode filenames."""
from pathlib import Path
import sys, json
import cv2
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'py'))
import seam_correct as s
root=Path(r'D:\Claude\masonjar-7.0.2-MC.1-improving-win32-x64\seam-review')
src=root/'202607.M554.M579.01.63 original.png'
img=cv2.imread(str(src),cv2.IMREAD_GRAYSCALE)
assert img is not None
h,w=img.shape
name=src.stem.removesuffix(' original')
recorded=[99,199,298,397,497]
horizontal=[124,248,372,495,619]
def grid(xs,ys):
    return {'vertical':{'boundaries_frac':[x/w for x in xs]},'horizontal':{'boundaries_frac':[y/h for y in ys]}}
estimated,ei=s.correct(img,band=4)
xs=ei['seam_positions']
report={'source':str(src),'shape':[h,w],'geometry_source':'20260910_141419.log 14:57:08, sidecar unverified','grid_estimated':ei,'experiments':{}}
def save(folder,mode,a):
    folder.mkdir(exist_ok=True,parents=True)
    assert cv2.imwrite(str(folder/(name+' '+mode+'.png')),a)
save(root,'Grid-estimated',estimated)
cases=[('recorded_default',recorded,horizontal,True,0),
       ('recorded_ramp4',recorded,horizontal,True,4),
       ('same_coordinates_ramp4',xs,horizontal,False,4),
       ('same_coordinates_ramp0',xs,horizontal,False,0),
       ('same_coordinates_vertical_control',xs,[],False,0)]
for tag,v,hs,refine,ramp in cases:
    out,info=s.correct_known_geometry(img,grid(v,hs),band=4,refine=refine,ramp_width=ramp)
    save(root if tag=='recorded_default' else root/tag,'Known-geometry',out)
    info['residual_at_grid_positions']=s._seam_diagnostics(img,out,xs,band=4)
    info['mean_absolute_difference_from_grid']=float(np.mean(np.abs(out.astype(float)-estimated)))
    report['experiments'][tag]=info
# A source overlay uses SVG so the original raster remains unchanged.
lines=[]
for x in recorded:
    lines.append(f'<line x1="{x}" x2="{x}" y1="0" y2="{h}" stroke="cyan" stroke-dasharray="5 4"/>')
for x in xs:
    lines.append(f'<line x1="{x}" x2="{x}" y1="0" y2="{h}" stroke="magenta" stroke-dasharray="2 4"/>')
import base64
encoded=base64.b64encode(src.read_bytes()).decode()
(root/'slice1-boundaries.svg').write_text(f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h+25}"><rect width="100%" height="100%" fill="black"/><image width="{w}" height="{h}" href="data:image/png;base64,{encoded}"/>'+''.join(lines)+f'<text x="5" y="{h+18}" fill="white" font-size="12">Cyan: logged geometry / Magenta: current grid-estimated</text></svg>',encoding='utf-8')
(root/'slice1-controlled-review.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
print('Grid positions:',xs)
for tag,info in report['experiments'].items():
    print(tag,info['seam_positions_vertical'],'steps',info['step_diagnostics_vertical'],'residual',[r['after'] for r in info['residual_at_grid_positions']],'MAE',round(info['mean_absolute_difference_from_grid'],3))

