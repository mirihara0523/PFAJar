"""Verify geometry offsets depend only on local seam neighborhoods."""
from pathlib import Path
import sys
import numpy as np
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'py'))
import seam_correct as s

img = np.full((80, 180), 80, dtype=np.uint8)
img[:, 60:120] = 100
img[:, 120:] = 90
grid = {'vertical': {'boundaries_frac': [60/180, 120/180]}}
base, info = s.correct_known_geometry(img, grid, refine=False)
assert [v['applied'] for v in info['step_diagnostics_vertical']] == [20, -10]
assert all(r['improved'] for r in info['seam_residuals'])
assert info['ramp_half_width'] == 0
assert np.unique(base).size == 1, 'Default must cancel abrupt tile steps completely'
# Both axes remain active and combine without introducing boundary ramps.
both = img.copy()
both[40:] += 15
both_grid = dict(grid, horizontal={'boundaries_frac':[0.5]})
both_result, both_info = s.correct_known_geometry(both, both_grid, refine=False)
assert np.unique(both_result).size == 1
assert both_info['n_seams_horizontal'] == 1
# Large anatomical changes inside a tile must not change any seam estimate.
changed = img.copy()
changed[:, 70:110] = 190
_, changed_info = s.correct_known_geometry(changed, grid, refine=False)
assert info['step_diagnostics_vertical'] == changed_info['step_diagnostics_vertical']
assert info['independent_offsets_vertical'] == changed_info['independent_offsets_vertical']
# Horizontal correction must be exactly the transposed vertical operation.
horizontal, hi = s.correct_known_geometry(img.T, {'horizontal': grid['vertical']}, refine=False)
np.testing.assert_array_equal(base.T, horizontal)
# No geometry is an identity operation, and background remains unchanged.
identity, _ = s.correct_known_geometry(img, {})
np.testing.assert_array_equal(img, identity)
background = img.copy()
background[:12] = 0
result, _ = s.correct_known_geometry(background, grid, refine=False)
np.testing.assert_array_equal(result[:12], background[:12])
# Unsupported boundaries must not introduce offsets.
unsupported = np.zeros((80, 180), dtype=np.uint8)
unsupported[:, :60] = 80
result, ui = s.correct_known_geometry(unsupported, grid, refine=False)
np.testing.assert_array_equal(result, unsupported)
assert all(v['applied'] == 0 for v in ui['step_diagnostics_vertical'])
print('PASS local estimates, distant anatomy independence, transpose, identity, background, missing support')
profile = np.zeros(179)
profile[61] = 10
profile[79] = 100  # Strong anatomy outside the allowed window.
assert s._refine_boundary(profile, 60, 4, 1, 180) == 62
assert s._refine_boundary(np.zeros(179), 60, 4, 1, 180) == 60
assert s._geometry_refine_margin([100, 200, 300], 400, 40000) == 3
assert s._geometry_refine_margin([100, 200, 300], 400, 400) == 5
assert s._geometry_refine_margin([100, 200, 300], 400) == 4
assert s._refine_boundary(profile, 60, 0, 1, 180) == 60
print('PASS coordinate convention, bounded refinement, flat profile, resolution scaling')
