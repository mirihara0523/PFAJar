"""Independent measurement-band and transition-half-width evaluation."""
from pathlib import Path
import sys, json, argparse
import numpy as np
import cv2
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'py'))
import seam_correct as s

def evaluate(img, grid):
    records = []
    for band in (2, 4, 8, 12, 16):
        reference = None
        for ramp in (0, 2, 4, 8, 12):
            out, info = s.correct_known_geometry(img, grid, band=band, ramp_width=ramp, refine=False)
            steps = info['step_diagnostics_vertical']
            if reference is None:
                reference = steps
            assert reference == steps, 'Ramp changed measured steps'
            positions = info['seam_positions_vertical']
            records.append({'band':band, 'ramp_half_width':ramp, 'steps':steps,
                            'fixed_band4_residual':s._seam_diagnostics(img,out,positions,band=4),
                            'adjacent_pixel_jump':s._seam_diagnostics(img,out,positions,band=1)})
    return records

if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--preview')
    ap.add_argument('--report', required=True)
    args = ap.parse_args()
    img = np.full((80,180),80,dtype=np.uint8)
    img[:,60:120] = 100
    img[:,120:] = 90
    grid = {'vertical':{'boundaries_frac':[60/180,120/180]}}
    records = evaluate(img,grid)
    clean, _ = s.correct_known_geometry(img,grid,refine=False,ramp_width=0)
    assert np.unique(clean).size == 1, 'Abrupt synthetic steps should cancel without ramp'
    report = {'synthetic':records}
    if args.preview:
        img = cv2.imread(args.preview,cv2.IMREAD_GRAYSCALE)
        assert img is not None
        h,w = img.shape
        grid = {'vertical':{'boundaries_frac':[b/w for b in [119,238,356,475]]},
                'horizontal':{'boundaries_frac':[b/h for b in [104,208,311,415,519]]}}
        report['preview_coordinate_note'] = 'Prior logged geometry, fixed without refinement; not verified against sidecar.'
        report['preview'] = evaluate(img,grid)
    Path(args.report).write_text(json.dumps(report,indent=2),encoding='utf-8')
    print('PASS ramp does not change measured steps; 25 combinations per image; fixed evaluation windows')
    for row in report.get('preview',[]):
        if row['band']==4 or row['ramp_half_width']==4:
            print(row['band'],row['ramp_half_width'],[r['after'] for r in row['fixed_band4_residual']])
