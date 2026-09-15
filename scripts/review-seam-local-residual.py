"""Review local position sensitivity with fixed original tissue masks."""
from pathlib import Path
import sys,json
import cv2,numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'py'))
import seam_correct as s
root=Path(r'D:\Claude\masonjar-7.0.2-MC.1-improving-win32-x64\seam-review')
name='202607.M554.M579.01.63'
a=cv2.imread(str(root/(name+' original.png')),0)
assert a is not None
mask=a>8
h,w=a.shape
v=[99,199,298,397,497]; hb=[124,248,372,495,619]
def run(bounds,ramp=0):
    return s.correct_known_geometry(a,{'vertical':{'boundaries_frac':[x/w for x in bounds]},'horizontal':{'boundaries_frac':[x/h for x in hb]}},refine=False,ramp_width=ramp)[0]
base=run(v); old=run(v,4)
report={'note':'Logged geometry; candidate positions are experiments, not confirmed physical seams. Fixed original tissue mask for paired measurements.','boundaries':[]}
for i,x in enumerate(v):
    item={'recorded_x':x,'before_paired':s.estimate_step(a,mask,x),'after_paired':s.estimate_step(base,mask,x),'ramp4_paired':s.estimate_step(old,mask,x),'candidates':[]}
    for candidate in range(x-2,x+3):
        bounds=v.copy(); bounds[i]=candidate
        result=run(bounds)
        # Score on a fixed neighborhood so shifting the measuring window cannot hide a seam.
        lo,hi=x-4,x+5
        pairmask=mask[:,lo:hi-1]&mask[:,lo+1:hi]
        delta=np.abs(np.diff(result[:,lo:hi].astype(float),axis=1))
        jumps=[s.estimate_step(result,mask,p,band=1) for p in range(x-2,x+3)]
        groups=[]
        for rows in np.array_split(np.arange(h),6):
            groups.append(round(s.estimate_step(a[rows],mask[rows],candidate),3))
        item['candidates'].append({'x':candidate,'step':s.estimate_step(a,mask,candidate),'height_steps':groups,'fixed_window_mean_gradient':float(delta[pairmask].mean()),'fixed_window_max_paired_jump':max(abs(t) for t in jumps)})
    report['boundaries'].append(item)
    print(json.dumps(item))
(root/'slice1-local-residual-review.json').write_text(json.dumps(report,indent=2),encoding='utf-8')

