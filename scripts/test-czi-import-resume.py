"""Regression checks for conservative interrupted-CZI resume eligibility."""
import os
import sys
import tempfile
from pathlib import Path

os.environ["MASONJAR_IO_FAIRSHARE"] = "0"
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "py"))
import czi_extract as c

signal = {
    "czi_path": "Y:/source/dotted.slice.czi", "scene_index": 0, "channel_index": 0,
    "slice_id": "dotted.slice", "role_key": "signal_somata",
    "channel": {"role": "signal_somata"},
}
dapi = {
    "czi_path": "Y:/source/dotted.slice.czi", "scene_index": 0, "channel_index": 2,
    "slice_id": "dotted.slice", "role_key": "dapi", "channel": {"role": "dapi"},
}
cfg = {"config_fingerprint": "stable-import-config"}
assert c.import_work_item_key(signal) != c.import_work_item_key(dapi)
assert c.import_work_signature(cfg, [signal, dapi]) == c.import_work_signature(cfg, [signal, dapi])
assert c.import_work_signature({"config_fingerprint": "changed"}, [signal, dapi]) != c.import_work_signature(cfg, [signal, dapi])

with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp)
    # A signal item needs its original TIFF and branch-specific preview.
    src = c.original_scans_path(root, signal["channel"], signal["slice_id"])
    preview = c.signal_preview_path(root, signal["slice_id"], signal["channel"])
    src.parent.mkdir(parents=True, exist_ok=True)
    preview.parent.mkdir(parents=True, exist_ok=True)
    src.write_bytes(b"tiff")
    assert not c.import_item_outputs_complete(root, signal)
    preview.write_bytes(b"png")
    assert c.import_item_outputs_complete(root, signal)

    # DAPI is resumable only after both user-facing PNG locations exist.
    dapi_src = c.original_scans_path(root, dapi["channel"], dapi["slice_id"])
    dapi_src.write_bytes(b"tiff")
    first = c.dapi_preview_path(root, dapi["slice_id"])
    second = c.orient_dapi_preview_path(root, dapi["slice_id"])
    first.parent.mkdir(parents=True, exist_ok=True)
    second.parent.mkdir(parents=True, exist_ok=True)
    first.write_bytes(b"png")
    assert not c.import_item_outputs_complete(root, dapi)
    second.write_bytes(b"png")
    assert c.import_item_outputs_complete(root, dapi)

print("CZI resume: work signature and complete role-specific output checks passed")
