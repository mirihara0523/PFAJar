import importlib.util
from pathlib import Path

path = Path(__file__).with_name("summarize-czi-import-log.py")
spec = importlib.util.spec_from_file_location("czi_log_summary", path)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

sample = [
    "[1/3] slice.1 role=signal_somata file=slice.1.czi scene=0 ch=0",
    "  Reading Z 2/10 (slice.1 ch 0)",
    "  [1/3] slice.1 signal_somata done in 5.2s [12:00:00]",
    "[2/3] slice.1 role=dapi file=slice.1.czi scene=0 ch=2",
    "  WARNING isolated CZI child exited code=-1073741819; completed items retained",
    "  retrying isolated CZI child (1 remaining item(s))",
    "  [2/3] slice.1 dapi done in 3.1s [12:00:10]",
    "[3/3] slice.2 role=dapi file=slice.2.czi scene=0 ch=2",
    "  ERROR extraction exhausted isolated retry for slice.2",
]
report = module.summarize_lines(sample)
assert report["total_items"] == 3
assert report["completed_items"] == 2
assert report["isolated_retries"] == 1
assert report["isolated_child_exit_codes"] == [-1073741819]
assert report["exhausted_items"] == ["slice.2"]
assert report["status"] == "interrupted_with_failures"
print("CZI import log summary: completion, retry, crash exit, and exhaustion parsing passed")
