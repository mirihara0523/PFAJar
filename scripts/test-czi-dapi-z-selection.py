"""Regression checks for DAPI's metadata-driven single-Z selection boundary."""
import contextlib
import io
import os
import sys
from pathlib import Path
from unittest.mock import patch

os.environ["MASONJAR_IO_FAIRSHARE"] = "0"
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "py"))
import czi_extract as c


with patch.object(c, "z_indices_with_data", return_value=[4]):
    captured = io.StringIO()
    with contextlib.redirect_stdout(captured):
        z_indices, mode = c.select_dapi_z_indices(object(), 0, 2)
    assert z_indices == [4]
    assert mode == "metadata_single"
    assert "dapi_z_selection mode=metadata_single channel=2 z=4" in captured.getvalue()


with patch.object(c, "z_indices_with_data", return_value=[0, 1, 2, 3, 4]):
    captured = io.StringIO()
    with contextlib.redirect_stdout(captured):
        z_indices, mode = c.select_dapi_z_indices(object(), 0, 2)
    assert z_indices == [0, 1, 2, 3, 4]
    assert mode == "ambiguous_metadata"
    assert "dapi_z_selection mode=ambiguous_metadata" in captured.getvalue()
    assert "retaining candidates" in captured.getvalue()


assert c._dapi_z_priority_order([0, 1, 2, 3, 4, 5, 6, 7, 8, 9]) == [4, 5, 3, 6, 2, 7, 1, 8, 0, 9]
assert c._dapi_z_priority_order([4]) == [4]
assert c._dapi_z_priority_order([2, 2, 4, 6]) == [4, 6, 2]  # duplicates dropped; equidistant ties favor the higher index


with patch.object(c, "z_indices_with_data", return_value=[4]):
    captured = io.StringIO()
    with contextlib.redirect_stdout(captured):
        resolved_z, mode = c.resolve_dapi_z(Path("."), "cfg.json", object(), Path("sample.czi"), 0, 2)
    assert resolved_z == 4
    assert mode == "metadata_single"
    # A single metadata candidate must be used directly with no probe subprocess.
    assert "dapi_z_probe" not in captured.getvalue()


class FakeProbeProcess:
    """Mimics one disposable --dapi-probe child (see test-czi-isolation.py).

    A crashing candidate emits no CZI_CHILD_DAPI_PROBE line at all (a native
    access violation kills the child before it can print) and exits with the
    Windows access-violation code; a clean read emits the protocol line and
    exits 0 or 1 depending on whether the plane came back non-empty.
    """

    def __init__(self, ok: bool, crash: bool = False):
        self.stdout = iter([] if crash else [f'CZI_CHILD_DAPI_PROBE:{{"ok":{"true" if ok else "false"}}}\n'])
        self._exit_code = -1073741819 if crash else (0 if ok else 1)

    def wait(self):
        return self._exit_code


def _probe_sequence(results):
    """results: list of (ok, crash) tuples consumed in call order."""
    calls = {"n": 0}

    def _popen(*_args, **_kwargs):
        ok, crash = results[calls["n"]]
        calls["n"] += 1
        return FakeProbeProcess(ok, crash)

    return _popen, calls


# Ambiguous metadata, first probed candidate (Z=4, nearest the confirmed
# ZEN-position-5 focal plane) reads a real plane: resolved immediately.
with patch.object(c, "z_indices_with_data", return_value=[0, 1, 2, 3, 4, 5]):
    popen, calls = _probe_sequence([(True, False)])
    with patch.object(c.subprocess, "Popen", popen):
        captured = io.StringIO()
        with contextlib.redirect_stdout(captured):
            resolved_z, mode = c.resolve_dapi_z(Path("."), "cfg.json", object(), Path("sample.czi"), 0, 2)
        assert resolved_z == 4
        assert mode == "probed"
        assert calls["n"] == 1


# Ambiguous metadata where the nearest candidate (Z=4) native-crashes in its
# disposable child: must be logged and the next candidate (Z=5) tried, not
# the whole batch aborted.
with patch.object(c, "z_indices_with_data", return_value=[0, 1, 2, 3, 4, 5]):
    popen, calls = _probe_sequence([(False, True), (True, False)])
    with patch.object(c.subprocess, "Popen", popen):
        captured = io.StringIO()
        with contextlib.redirect_stdout(captured):
            resolved_z, mode = c.resolve_dapi_z(Path("."), "cfg.json", object(), Path("sample.czi"), 0, 2)
        assert resolved_z == 5
        assert mode == "probed"
        assert calls["n"] == 2
        assert "dapi_z_probe_crash" in captured.getvalue() or "dapi_z_probe_failed" in captured.getvalue()


# Every candidate crashes or reads empty: only this DAPI item fails, callers
# must continue with the rest of the CZI / batch rather than aborting.
with patch.object(c, "z_indices_with_data", return_value=[3, 4, 5]):
    popen, calls = _probe_sequence([(False, True), (False, True), (False, False)])
    with patch.object(c.subprocess, "Popen", popen):
        captured = io.StringIO()
        with contextlib.redirect_stdout(captured):
            resolved_z, mode = c.resolve_dapi_z(Path("."), "cfg.json", object(), Path("sample.czi"), 0, 2)
        assert resolved_z is None
        assert mode == "all_candidates_failed"
        assert calls["n"] == 3
        assert "all_candidates_failed" in captured.getvalue()


class _FakeCzi:
    def close(self):
        pass


# The first ambiguous candidate is now read by the ordinary isolated extract
# child, rather than a prior --dapi-probe child.  A successful candidate must
# be handed to extract_z_stack as its only Z and must not re-run metadata
# selection inside that child.
with contextlib.ExitStack() as stack:
    root = Path(".")
    observed = []

    def _extract(*args, **_kwargs):
        observed.append(list(args[3]))

    stack.enter_context(patch.object(c, "CziFile", lambda _path: _FakeCzi()))
    stack.enter_context(patch.object(c, "normalized_dim_blocks", lambda _czi: []))
    stack.enter_context(patch.object(c, "assess_mosaic_import", lambda *_a, **_k: {"is_mosaic": True}))
    stack.enter_context(patch.object(c, "z_indices_with_data", side_effect=AssertionError("candidate must bypass metadata")))
    stack.enter_context(patch.object(c, "original_scans_path", lambda _root, _ch, sid: root / f"{sid}.tif"))
    stack.enter_context(patch.object(c, "extract_z_stack", _extract))
    with patch.object(Path, "is_file", return_value=True), contextlib.redirect_stdout(io.StringIO()):
        assert c._isolated_extract_file_child(root, {}, {"items": [{
            "ordinal": 1,
            "total": 1,
            "item": {
                "czi_path": "sample.czi", "slice_id": "sample", "role_key": "dapi",
                "channel": {"role": "dapi"}, "scene_index": 0, "channel_index": 2,
                "dapi_candidate_z": 4,
            },
            "stream_max_path": "",
        }]}) == 0
    assert observed == [[4]]


print(
    "DAPI Z selection: single metadata plane, ambiguous fallback, priority "
    "ordering, crash-safe probe resolution, and extract-child candidate use passed"
)
