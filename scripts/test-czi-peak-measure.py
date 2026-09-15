"""Pure regression checks for exact/sample peak measurement arithmetic."""
import importlib.util
from pathlib import Path
import numpy as np

script = Path(__file__).with_name("measure-czi-peak-modes.py")
spec = importlib.util.spec_from_file_location("peak_measure", script)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

hist = np.zeros(65536, dtype=np.uint64)
hist[0] = 90
hist[100] = 5
hist[500] = 3
hist[1000] = 2
same = module.compare_peak_histogram(hist, 1000, 1000)
assert same["sampled_scale_saturated_pixels"] == 0
assert same["u8_mapping_changed_pixels"] == 0

lower = module.compare_peak_histogram(hist, 1000, 500)
assert lower["peak_delta"] == -500
assert lower["sampled_scale_saturated_pixels"] == 2
assert lower["u8_mapping_changed_pixels"] == 8
print("CZI peak measurement: histogram saturation and uint8 mapping comparison passed")
