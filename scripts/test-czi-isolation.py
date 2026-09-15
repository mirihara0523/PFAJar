"""Regression checks for the crash-isolating CZI child protocol."""
import contextlib
import io
import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

import numpy as np
import tifffile

os.environ["MASONJAR_IO_FAIRSHARE"] = "0"
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "py"))
import czi_extract as c

c.np, c.tiff, c.cv2 = np, tifffile, object()

class FakeCzi:
    def close(self):
        pass

with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp)
    source = root / "sample.czi"
    source.write_bytes(b"fixture")
    payload = {
        "preview_scale": 0.05,
        "items": [
            {
                "ordinal": 2,
                "total": 3,
                "item": {
                    "czi_path": str(source), "slice_id": "dotted.slice",
                    "role_key": "signal_somata", "channel": {"role": "signal_somata"},
                    "scene_index": 0, "channel_index": 1,
                },
                "stream_max_path": str(root / "stage.tif"),
            }
        ],
    }
    calls = []
    mosaic_call_kwargs = []
    def fake_extract(*args, **kwargs):
        calls.append((args, kwargs))
        Path(kwargs["stream_max_path"]).parent.mkdir(parents=True, exist_ok=True)
        Path(kwargs["stream_max_path"]).write_bytes(b"max")
    def fake_assess(*_args, **kwargs):
        mosaic_call_kwargs.append(kwargs)
        return {"is_mosaic": True}
    output = io.StringIO()
    with patch.object(c, "CziFile", lambda _p: FakeCzi()), \
         patch.object(c, "normalized_dim_blocks", lambda _c: []), \
         patch.object(c, "assess_mosaic_import", fake_assess), \
         patch.object(c, "z_indices_with_data", lambda *_a: [0]), \
         patch.object(c, "original_scans_path", lambda _r, _ch, sid: root / f"{sid}.tif"), \
         patch.object(c, "signal_preview_path", lambda _r, sid, _ch: root / f"{sid}.png"), \
         patch.object(c, "extract_z_stack", fake_extract), \
         contextlib.redirect_stdout(output):
        assert c._isolated_extract_file_child(root, {}, payload) == 0
    events = [json.loads(line.split(":", 1)[1]) for line in output.getvalue().splitlines() if line.startswith("CZI_CHILD_ITEM:")]
    assert len(calls) == 1 and events[0]["ok"] and events[0]["ordinal"] == 2
    assert events[0]["stream_max_staged"] is True
    assert mosaic_call_kwargs == [{"sample_read": True, "sample_scale": 0.05}]

class FakeProcess:
    def __init__(self):
        self.stdout = iter([
            'LOG: child detail\n',
            'CZI_CHILD_ITEM:{"ok":true,"ordinal":4}\n',
            'CZI_CHILD_FILE_DONE:{"ok":true}\n',
        ])
    def wait(self):
        return -1073741819  # Windows access violation; parent must retain event.

relayed = io.StringIO()
with patch.object(c.subprocess, "Popen", lambda *_a, **_k: FakeProcess()), contextlib.redirect_stdout(relayed):
    code, events = c._run_isolated_extract_file(Path("."), "cfg.json", {"items": []})
assert code == -1073741819 and events == [{"ok": True, "ordinal": 4}]
assert "LOG: child detail" in relayed.getvalue()


class FakeStdin:
    def __init__(self):
        self.writes = []
        self.closed = False
    def write(self, value):
        self.writes.append(value)
    def flush(self):
        pass
    def close(self):
        self.closed = True


class FakePersistentProcess:
    pid = 1234
    returncode = None
    def __init__(self):
        self.stdin = FakeStdin()
        self.stdout = iter([
            'LOG: persistent child detail\n',
            'CZI_CHILD_ITEM:{"ok":true,"ordinal":7}\n',
            'CZI_CHILD_JOB_DONE:{"ok":true,"code":0}\n',
        ])
    def poll(self):
        return None
    def wait(self, timeout=None):
        return 0


persistent_proc = FakePersistentProcess()
with patch.object(c.subprocess, "Popen", lambda *_a, **_k: persistent_proc):
    worker = c._PersistentExtractWorker(Path("."), "cfg.json")
    code, events = worker.run({"items": [{"ordinal": 7}]})
    worker.close()
assert code == 0 and events == [{"ok": True, "ordinal": 7}]
assert json.loads(persistent_proc.stdin.writes[0]) == {"items": [{"ordinal": 7}]}
assert persistent_proc.stdin.closed


failed_persistent_proc = FakePersistentProcess()
failed_persistent_proc.stdout = iter(['CZI_CHILD_JOB_DONE:{"ok":false,"code":2}\n'])
with patch.object(c.subprocess, "Popen", lambda *_a, **_k: failed_persistent_proc):
    worker = c._PersistentExtractWorker(Path("."), "cfg.json")
    code, events = worker.run({"items": []})
    # A normal Python job failure must return immediately so the supervisor can
    # select its disposable fallback; the server itself remains reusable.
    assert code == 2 and events == [] and not failed_persistent_proc.stdin.closed
    worker.close()


server_calls = []
server_output = io.StringIO()
with patch.object(c, "_isolated_extract_file_child", lambda _root, _cfg, payload: server_calls.append(payload) or 0), \
     patch.object(sys, "stdin", io.StringIO('{"file":"one"}\n{"file":"two"}\n')), \
     contextlib.redirect_stdout(server_output):
    assert c._run_isolated_worker(Path("."), {}) == 0
assert server_calls == [{"file": "one"}, {"file": "two"}]
assert server_output.getvalue().count("CZI_CHILD_JOB_DONE:") == 2
print("CZI isolation: child item completion survives native-process exit and is relayed")
