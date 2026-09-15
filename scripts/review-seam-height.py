"""Height variation review with held-out 16-row blocks; no production edits."""
from pathlib import Path
import json
import cv2
import numpy as np

root=Path(r'D:\Claude\masonjar-7.0.2-MC.1-improving-win32-x64\seam-review')
a=cv2.imread(str(root/'202607.M554.M579.01.63 original.png'),0)
assert a is not None
a=a.astype(float); h,w=a.shape
y=np.arange(h)
report={'note':'Logged fixed seam positions. Local fit uses +/-64 rows, at least 20 training rows; alternating 16-row blocks held out in both directions. These are brightness-step prediction errors, not proof of preserved anatomy.','seams':[]}
for x in [99,199,298,397,497]:
    entry={'x':x,'bands':[]}
    for band in [2,4,8]:
        l=a[:,x-band:x]; r=a[:,x:x+band]
        lm=l>8; rm=r>8
        valid=(lm.sum(1)>=max(1,band//2))&(rm.sum(1)>=max(1,band//2))
        dif=(r*rm).sum(1)/np.maximum(rm.sum(1),1)-(l*lm).sum(1)/np.maximum(lm.sum(1),1)
        groups=[]
        for rows in np.array_split(y,6):
            v=rows[valid[rows]]
            groups.append({'y_range':[int(rows[0]),int(rows[-1])],'n':len(v),'median':round(float(np.median(dif[v])),3) if len(v)>=10 else None})
        scalar_errors=[]; local_errors=[]; supported=0
        for fold in [0,1]:
            train=valid&((y//16)%2==fold)
            test=valid&~train
            scalar=float(np.median(dif[train]))
            for row in y[test]:
                nearby=train&(abs(y-row)<=64)
                if nearby.sum()<20:
                    continue
                pred=float(np.median(dif[nearby]))
                scalar_errors.append(abs(dif[row]-scalar)); local_errors.append(abs(dif[row]-pred)); supported+=1
        baseline=float(np.mean(scalar_errors)); adaptive=float(np.mean(local_errors))
        entry['bands'].append({'band':band,'height_groups':groups,'heldout_rows':supported,'scalar_MAE':round(baseline,3),'local_MAE':round(adaptive,3),'improvement_percent':round(100*(baseline-adaptive)/baseline,2)})
    report['seams'].append(entry)
    print(x,[(b['band'],b['improvement_percent']) for b in entry['bands']], 'height band4',entry['bands'][1]['height_groups'])
(root/'slice1-height-review.json').write_text(json.dumps(report,indent=2),encoding='utf-8')

