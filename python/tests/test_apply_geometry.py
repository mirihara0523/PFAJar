"""Tests for apply_geometry OpenCV transforms."""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest
import tifffile as tiff

REPO_PY = Path(__file__).resolve().parents[2] / "py"
sys.path.insert(0, str(REPO_PY))

from apply_geometry import (  # noqa: E402
    apply_ops_to_array,
    compose_ops,
    compose_ops_from_spec,
    ops_from_string_list,
)


def test_compose_rotate_flip() -> None:
    ops = compose_ops(90, True, False)
    assert ("rotate", 90) in ops
    assert ("flip_x", True) in ops


def test_ops_list_matches_legacy_compose() -> None:
    arr = np.arange(24, dtype=np.uint8).reshape(4, 6)
    legacy_ops = compose_ops(90, True, False)
    string_ops = ops_from_string_list(["rot90", "flipX"])
    assert string_ops == legacy_ops
    assert np.array_equal(
        apply_ops_to_array(arr, compose_ops_from_spec({"ops": ["rot90", "flipX"]})),
        apply_ops_to_array(arr, legacy_ops),
    )


def test_compose_ops_from_spec_legacy_fallback() -> None:
    spec = {"rotate": 90, "flipX": True, "flipY": False}
    assert compose_ops_from_spec(spec) == compose_ops(90, True, False)


def test_apply_ops_rot90_clockwise_non_square() -> None:
    """rotate=90 must match CSS clockwise 90° (np.rot90 k=-1), not counter-clockwise k=1."""
    arr = np.arange(24, dtype=np.uint8).reshape(4, 6)
    out = apply_ops_to_array(arr, compose_ops(90, False, False))
    assert out.shape == (6, 4)
    expected = np.rot90(arr, k=-1)
    assert np.array_equal(out, expected)
    assert out[0, 0] == 18


def test_paths_for_slice_includes_dapi_png_not_tif(tmp_path: Path) -> None:
    from bundle_slice_paths import paths_for_slice

    bundle = tmp_path / "Brain_masonjar"
    slice_id = "M528_s001"
    dapi_png = bundle / "data/counting/00_dapi" / f"{slice_id}.png"
    dapi_tif = bundle / "data/counting/00_dapi" / f"{slice_id}.tif"
    prev_png = bundle / "data/counting/_previews" / f"{slice_id}_somata.png"
    prev_tif = bundle / "data/counting/_previews" / f"{slice_id}_somata.tif"
    for p in (dapi_png, dapi_tif, prev_png, prev_tif):
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"x")
    cfg = {"channels": [{"role": "signal_somata", "keep": True}]}
    paths = paths_for_slice(bundle, slice_id, cfg)
    names = {p.name for p in paths}
    assert f"{slice_id}.png" in names
    assert f"{slice_id}.tif" not in names
    assert f"{slice_id}_somata.png" in names
    assert f"{slice_id}_somata.tif" not in names


def test_collect_geometry_jobs_skips_identity(tmp_path: Path) -> None:
    from apply_geometry import collect_geometry_jobs

    bundle = tmp_path / "Brain_masonjar"
    cfg = {"channels": []}
    geometry = {
        "A": {"ops": []},
        "B": {"ops": ["rot90"]},
    }
    jobs = collect_geometry_jobs(bundle, geometry, cfg)
    assert [job[0] for job in jobs] == []


def test_collect_geometry_jobs_scoped_reextract_skips_dapi(tmp_path: Path) -> None:
    from apply_geometry import collect_geometry_jobs

    bundle = tmp_path / "Brain_masonjar"
    slice_id = "M528_s001"
    dapi_png = bundle / "data/counting/00_dapi" / f"{slice_id}.png"
    prev_dapi = bundle / "data/counting/_previews" / f"{slice_id}_dapi.png"
    prev_somata = bundle / "data/counting/_previews" / f"{slice_id}_somata.png"
    orig = bundle / "data/original_scans/somata" / f"{slice_id}.tif"
    for p in (dapi_png, prev_dapi, prev_somata, orig):
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"x")

    cfg = {
        "channels": [{"role": "signal_somata", "keep": True}],
        "reextract_geometry_scope": {
            slice_id: ["signal_somata"],
        },
    }
    geometry = {slice_id: {"ops": ["rot90"]}}
    jobs = collect_geometry_jobs(bundle, geometry, cfg)
    assert len(jobs) == 1
    targets = {p.name for p in jobs[0][2]}
    assert f"{slice_id}_somata.png" in targets
    assert f"{slice_id}.png" not in targets
    assert f"{slice_id}_dapi.png" not in targets


def test_transform_file_roundtrip(tmp_path: Path) -> None:
    from apply_geometry import transform_file

    src = np.ones((4, 6), dtype=np.uint8) * 7
    path = tmp_path / "tile.tif"
    tiff.imwrite(path, src)
    transform_file(path, compose_ops(180, False, False))
    loaded = tiff.imread(path)
    assert loaded.shape == src.shape


def test_transform_zstack_rot90_per_plane(tmp_path: Path) -> None:
    from apply_geometry import transform_file

    z, h, w = 3, 4, 6
    stack = np.arange(z * h * w, dtype=np.uint8).reshape(z, h, w)
    path = tmp_path / "stack.tif"
    tiff.imwrite(path, stack, photometric="minisblack")
    transform_file(path, compose_ops(90, False, False))
    loaded = tiff.imread(path)
    assert loaded.shape == (z, w, h)
    for zi in range(z):
        expected = np.rot90(stack[zi], k=-1)
        assert np.array_equal(loaded[zi], expected)


def test_transform_zstack_flip_x(tmp_path: Path) -> None:
    from apply_geometry import transform_file

    stack = np.arange(24, dtype=np.uint8).reshape(2, 3, 4)
    path = tmp_path / "flip.tif"
    tiff.imwrite(path, stack, photometric="minisblack")
    transform_file(path, compose_ops(0, True, False))
    loaded = tiff.imread(path)
    assert loaded.shape == stack.shape
    for zi in range(2):
        assert np.array_equal(loaded[zi], np.fliplr(stack[zi]))


def test_geometry_progress_path_key(tmp_path: Path) -> None:
    from geometry_apply_progress import path_key

    bundle = tmp_path / "Brain_masonjar"
    f = bundle / "data" / "counting" / "00_dapi" / "S1.png"
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_bytes(b"x")
    assert path_key(bundle, f) == "data/counting/00_dapi/S1.png"


def test_double_apply_rot90_changes_pixels_twice(tmp_path: Path) -> None:
    """Applying the same rotation twice stacks transforms — geometry must reset after apply."""
    import cv2

    from apply_geometry import transform_file

    arr = np.arange(12, dtype=np.uint8).reshape(3, 4)
    path = tmp_path / "slice.png"
    cv2.imwrite(str(path), arr)
    once = arr.copy()
    transform_file(path, compose_ops(90, False, False))
    after_once = cv2.imread(str(path), cv2.IMREAD_UNCHANGED)
    assert not np.array_equal(after_once, once)
    transform_file(path, compose_ops(90, False, False))
    after_twice = cv2.imread(str(path), cv2.IMREAD_UNCHANGED)
    assert not np.array_equal(after_twice, after_once)
    expected_twice = np.rot90(np.rot90(once, k=-1), k=-1)
    assert np.array_equal(after_twice, expected_twice)


def test_preflight_probe_uses_tiff_file_not_full_read(tmp_path: Path, monkeypatch) -> None:
    from apply_geometry import _probe_target, collect_geometry_jobs, preflight_log

    bundle = tmp_path / "Brain_masonjar"
    z, h, w = 4, 8, 10
    stack = np.arange(z * h * w, dtype=np.uint8).reshape(z, h, w)
    orig = bundle / "data/original_scans/somata/M1.tif"
    orig.parent.mkdir(parents=True, exist_ok=True)
    tiff.imwrite(orig, stack, photometric="minisblack")

    def _boom(_path):
        raise ValueError("whole-file imread should not run in preflight")

    monkeypatch.setattr("apply_geometry.tiff.imread", _boom)

    ok, detail = _probe_target(orig)
    assert ok is True
    assert "Z-stack" in detail
    assert "Z=4" in detail

    cfg = {"channels": [{"role": "signal_somata", "keep": True}]}
    geometry = {"M1": {"ops": ["rot90"]}}
    jobs = collect_geometry_jobs(bundle, geometry, cfg)
    total, failed = preflight_log(jobs, bundle)
    assert total >= 1
    assert failed == []


def test_preflight_aborts_on_missing_target(tmp_path: Path) -> None:
    from apply_geometry import _probe_target, collect_geometry_jobs, preflight_log

    bundle = tmp_path / "Brain_masonjar"
    missing = bundle / "data/counting/_previews/M1_somata.png"
    missing.parent.mkdir(parents=True, exist_ok=True)
    cfg = {"channels": [{"role": "signal_somata", "keep": True}]}
    geometry = {"M1": {"ops": ["rot90"]}}
    jobs = collect_geometry_jobs(bundle, geometry, cfg)
    assert jobs == []
    ok, detail = _probe_target(missing)
    assert ok is False
    assert "missing" in detail


def test_repair_derivatives_from_original(tmp_path: Path) -> None:
    from apply_geometry import run_repair_jobs

    bundle = tmp_path / "Brain_masonjar"
    slice_id = "M1"
    z, h, w = 2, 6, 8
    stack = np.arange(z * h * w, dtype=np.uint8).reshape(z, h, w)
    orig = bundle / "data/original_scans/somata" / f"{slice_id}.tif"
    prev = bundle / "data/counting/_previews" / f"{slice_id}_somata.png"
    orig.parent.mkdir(parents=True, exist_ok=True)
    prev.parent.mkdir(parents=True, exist_ok=True)
    tiff.imwrite(orig, stack, photometric="minisblack")
    prev.write_bytes(b"old")

    cfg = {
        "preview_scale": 0.5,
        "repair_targets": [
            {
                "slice_id": slice_id,
                "branch": "somata",
                "rel_path": "data/counting/_previews/M1_somata.png",
                "strategy": "derivatives_from_original",
                "ops": ["rot90"],
            },
        ],
    }
    changed, _bytes, failed, total = run_repair_jobs(bundle, cfg)
    assert failed == []
    assert changed == 1
    assert total == 1
    loaded = tiff.imread(orig)
    assert loaded.shape == (z, w, h)
    assert prev.stat().st_size > 4


def test_repair_derivatives_with_io_fairshare(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import json

    import io_fairshare

    coord = tmp_path / "fairshare"
    coord.mkdir()
    (coord / "registry").mkdir()
    (coord / "config.json").write_text(
        json.dumps(
            {
                "enabled": True,
                "link_mbps": 1000,
                "headroom": 0.85,
                "min_mbps_per_job": 25,
                "max_mbps_per_job": "auto",
                "small_file_bytes": 256 * 1024,
                "stale_seconds": 30,
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("MASONJAR_IO_FAIRSHARE_DIR", str(coord))
    monkeypatch.setenv("MASONJAR_IO_FAIRSHARE", "1")
    monkeypatch.setenv("MASONJAR_IO_JOB_ID", "test-geo-repair")
    monkeypatch.setenv("MASONJAR_IO_JOB_LABEL", "test")
    io_fairshare.deactivate()
    assert io_fairshare.activate()
    monkeypatch.setattr(io_fairshare, "_should_throttle", lambda _p: True)

    from apply_geometry import run_repair_jobs

    bundle = tmp_path / "Brain_masonjar"
    slice_id = "M1"
    z, h, w = 2, 6, 8
    stack = np.arange(z * h * w, dtype=np.uint8).reshape(z, h, w)
    orig = bundle / "data/original_scans/somata" / f"{slice_id}.tif"
    prev = bundle / "data/counting/_previews" / f"{slice_id}_somata.png"
    orig.parent.mkdir(parents=True, exist_ok=True)
    prev.parent.mkdir(parents=True, exist_ok=True)
    tiff.imwrite(orig, stack, photometric="minisblack")
    prev.write_bytes(b"old")

    cfg = {
        "preview_scale": 0.5,
        "repair_targets": [
            {
                "slice_id": slice_id,
                "branch": "somata",
                "rel_path": "data/counting/_previews/M1_somata.png",
                "strategy": "derivatives_from_original",
                "ops": ["rot90"],
            },
        ],
    }
    changed, _bytes, failed, total = run_repair_jobs(bundle, cfg)
    io_fairshare.deactivate()
    assert failed == []
    assert changed == 1
    assert total == 1
    loaded = tiff.imread(orig)
    assert loaded.shape == (z, w, h)
    assert prev.stat().st_size > 4


def test_repair_dapi_derivatives_flat_zstack(tmp_path: Path) -> None:
    from apply_geometry import run_repair_jobs

    bundle = tmp_path / "Brain_masonjar"
    slice_id = "M1"
    h, w = 8, 10
    plane = np.arange(h * w, dtype=np.uint8).reshape(h, w)
    orig = bundle / "data/original_scans" / f"{slice_id}.tif"
    prev = bundle / "data/counting/_previews" / f"{slice_id}_dapi.png"
    dapi = bundle / "data/counting/00_dapi" / f"{slice_id}.png"
    orig.parent.mkdir(parents=True, exist_ok=True)
    prev.parent.mkdir(parents=True, exist_ok=True)
    dapi.parent.mkdir(parents=True, exist_ok=True)
    tiff.imwrite(orig, plane, photometric="minisblack")
    prev.write_bytes(b"old")
    dapi.write_bytes(b"old")

    cfg = {
        "preview_scale": 0.5,
        "repair_targets": [
            {
                "slice_id": slice_id,
                "branch": "dapi",
                "rel_path": "data/counting/_previews/M1_dapi.png",
                "strategy": "derivatives_from_original",
                "ops": ["rot90"],
            },
        ],
    }
    changed, _bytes, failed, total = run_repair_jobs(bundle, cfg)
    assert failed == []
    assert changed == 1
    assert total == 1
    assert dapi.stat().st_size > 4
    assert prev.stat().st_size > 4


def test_repair_dapi_fallback_no_zstack(tmp_path: Path) -> None:
    import cv2

    from apply_geometry import run_repair_jobs

    bundle = tmp_path / "Brain_masonjar"
    slice_id = "M1"
    h, w = 4, 6
    plane = np.arange(h * w, dtype=np.uint8).reshape(h, w)
    prev = bundle / "data/counting/_previews" / f"{slice_id}_dapi.png"
    dapi = bundle / "data/counting/00_dapi" / f"{slice_id}.png"
    prev.parent.mkdir(parents=True, exist_ok=True)
    dapi.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(prev), plane)
    cv2.imwrite(str(dapi), plane)

    cfg = {
        "preview_scale": 0.5,
        "repair_targets": [
            {
                "slice_id": slice_id,
                "branch": "dapi",
                "rel_path": "data/counting/_previews/M1_dapi.png",
                "strategy": "derivatives_from_original",
                "ops": ["rot90"],
            },
        ],
    }
    changed, _bytes, failed, total = run_repair_jobs(bundle, cfg)
    assert failed == []
    assert changed == 1
    assert total == 1
    after_prev = cv2.imread(str(prev), cv2.IMREAD_UNCHANGED)
    expected = apply_ops_to_array(plane, [("rotate", 90)])
    np.testing.assert_array_equal(after_prev, expected)


def test_geometry_history_ops_to_js_list() -> None:
    from geometry_history import ops_to_js_list

    assert ops_to_js_list([("rotate", 90), ("flip_x", True), ("flip_y", True)]) == [
        "rot90",
        "flipX",
        "flipY",
    ]


def test_apply_geometry_writes_history_on_success(tmp_path: Path) -> None:
    import json
    import subprocess
    import sys

    import cv2

    bundle = tmp_path / "Brain_masonjar"
    slice_id = "M1"
    dapi = bundle / "data/counting/00_dapi" / f"{slice_id}.png"
    prev = bundle / "data/counting/_previews" / f"{slice_id}_dapi.png"
    for p in (dapi, prev):
        p.parent.mkdir(parents=True, exist_ok=True)
        cv2.imwrite(str(p), np.ones((4, 6), dtype=np.uint8) * 9)

    meta = bundle / ".masonjar"
    meta.mkdir(parents=True, exist_ok=True)
    cfg = {
        "geometry": {slice_id: {"ops": ["rot90"]}},
        "channels": [],
        "apply_source": "test",
        "geometry_hash": "abc",
        "config_fingerprint": "fp",
    }
    cfg_path = meta / "apply_test_config.json"
    cfg_path.write_text(json.dumps({"czi_import": cfg}), encoding="utf-8")

    script = REPO_PY / "apply_geometry.py"
    proc = subprocess.run(
        [sys.executable, str(script), "-b", str(bundle), "-j", str(cfg_path)],
        cwd=str(REPO_PY),
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr

    history_path = meta / "geometry_history.jsonl"
    assert history_path.is_file()
    lines = [json.loads(line) for line in history_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert any(entry.get("kind") == "file" and entry.get("slice_id") == slice_id for entry in lines)
    assert any(entry.get("kind") == "run" and entry.get("ok") is True for entry in lines)
