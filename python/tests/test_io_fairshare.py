"""Tests for py/io_fairshare.py adaptive bandwidth fair-share."""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pytest

PY_ROOT = Path(__file__).resolve().parents[1] / ".." / "py"
sys.path.insert(0, str(PY_ROOT.resolve()))

import io_fairshare  # noqa: E402


@pytest.fixture
def coordinator(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("MASONJAR_IO_FAIRSHARE_DIR", str(tmp_path))
    (tmp_path / "config.json").write_text(
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
    (tmp_path / "registry").mkdir()
    monkeypatch.setenv("MASONJAR_IO_FAIRSHARE", "1")
    monkeypatch.setenv("MASONJAR_IO_LINK_MBPS", "1000")
    monkeypatch.setenv("MASONJAR_IO_JOB_ID", "test-job")
    monkeypatch.setenv("MASONJAR_IO_JOB_LABEL", "test")
    return tmp_path


def test_compute_limit_single_job(coordinator: Path):
    io_fairshare._coordinator_dir = str(coordinator)
    assert io_fairshare.compute_limit_mbps() == pytest.approx(850.0, rel=0.01)


def test_compute_limit_splits_active_jobs(coordinator: Path):
    io_fairshare._coordinator_dir = str(coordinator)
    reg = coordinator / "registry"
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    for idx in range(3):
        (reg / f"job{idx}.json").write_text(
            json.dumps(
                {
                    "job_id": f"job{idx}",
                    "pid": 1000 + idx,
                    "user": "u",
                    "hostname": "h",
                    "label": "max",
                    "started_at": now,
                    "last_heartbeat": now,
                }
            ),
            encoding="utf-8",
        )
    limit = io_fairshare.compute_limit_mbps()
    assert limit == pytest.approx(850.0 / 3.0, rel=0.01)


def test_should_throttle_unc_on_windows(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    assert io_fairshare._should_throttle(r"\\nas\share\file.tif") is True
    assert io_fairshare._should_throttle(r"C:\local\file.tif") is False


def test_should_throttle_configured_prefix(monkeypatch, coordinator: Path):
    io_fairshare._coordinator_dir = str(coordinator)
    cfg = json.loads((coordinator / "config.json").read_text(encoding="utf-8"))
    cfg["nas_path_prefixes"] = ["Z:"]
    (coordinator / "config.json").write_text(json.dumps(cfg), encoding="utf-8")
    monkeypatch.setattr(sys, "platform", "win32")
    assert io_fairshare._should_throttle(r"Z:\lab\project\file.tif") is True


def test_small_file_bypass(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("MASONJAR_IO_FAIRSHARE", "0")
    p = tmp_path / "tiny.bin"
    p.write_bytes(b"hello")
    assert io_fairshare.throttled_read_bytes(p) == b"hello"


def test_throttled_tiff_imwrite_roundtrip(tmp_path: Path, coordinator: Path, monkeypatch):
    import numpy as np
    import tifffile

    monkeypatch.setenv("MASONJAR_IO_FAIRSHARE", "1")
    io_fairshare.deactivate()
    assert io_fairshare.activate()
    monkeypatch.setattr(io_fairshare, "_should_throttle", lambda _p: True)

    path = tmp_path / "stack.tif"
    arr = np.arange(24, dtype=np.uint8).reshape(2, 3, 4)
    tifffile.imwrite(path, arr, photometric="minisblack")
    loaded = tifffile.imread(path)
    np.testing.assert_array_equal(loaded, arr)
    io_fairshare.deactivate()


def test_activate_deactivate_rebind_job_id(coordinator: Path, monkeypatch):
    """Worker reuse: deactivate then activate with a new job id replaces registry."""
    reg = coordinator / "registry"
    monkeypatch.setenv("MASONJAR_IO_FAIRSHARE", "1")
    monkeypatch.setenv("MASONJAR_IO_JOB_ID", "job-a")
    monkeypatch.setenv("MASONJAR_IO_JOB_LABEL", "apply_geometry")
    io_fairshare.deactivate()
    assert io_fairshare.activate()
    assert (reg / "job-a.json").is_file()
    assert not (reg / "job-b.json").is_file()

    io_fairshare.deactivate()
    assert not (reg / "job-a.json").is_file()
    assert io_fairshare._activated is False

    monkeypatch.setenv("MASONJAR_IO_JOB_ID", "job-b")
    monkeypatch.setenv("MASONJAR_IO_JOB_LABEL", "tissue_cleanup")
    assert io_fairshare.activate()
    assert (reg / "job-b.json").is_file()
    assert not (reg / "job-a.json").is_file()
    payload = json.loads((reg / "job-b.json").read_text(encoding="utf-8"))
    assert payload["job_id"] == "job-b"
    assert payload["label"] == "tissue_cleanup"
    io_fairshare.deactivate()


def test_keep_registry_on_deactivate(coordinator: Path, monkeypatch):
    """Parent project_index session: Python must not unregister Node-owned entry."""
    reg = coordinator / "registry"
    monkeypatch.setenv("MASONJAR_IO_FAIRSHARE", "1")
    monkeypatch.setenv("MASONJAR_IO_JOB_ID", "project-index-1")
    monkeypatch.setenv("MASONJAR_IO_JOB_LABEL", "project_index")
    monkeypatch.setenv("MASONJAR_IO_KEEP_REGISTRY", "1")
    io_fairshare.deactivate()
    assert io_fairshare.activate()
    assert (reg / "project-index-1.json").is_file()
    io_fairshare.deactivate()
    assert io_fairshare._activated is False
    assert (reg / "project-index-1.json").is_file()
    monkeypatch.delenv("MASONJAR_IO_KEEP_REGISTRY", raising=False)
    (reg / "project-index-1.json").unlink(missing_ok=True)


def test_account_nas_open_consumes_when_throttling(tmp_path: Path, coordinator: Path, monkeypatch):
    monkeypatch.setenv("MASONJAR_IO_FAIRSHARE", "1")
    io_fairshare.deactivate()
    assert io_fairshare.activate()
    monkeypatch.setattr(io_fairshare, "_should_throttle", lambda _p: True)
    big = tmp_path / "big.tif"
    big.write_bytes(b"x" * (512 * 1024))
    before = io_fairshare._BUCKET.bytes_total()
    io_fairshare.account_nas_open(big)
    after = io_fairshare._BUCKET.bytes_total()
    assert after - before == 512 * 1024
    # Small files under bypass threshold are no-ops.
    tiny = tmp_path / "tiny.tif"
    tiny.write_bytes(b"y" * 100)
    mid = io_fairshare._BUCKET.bytes_total()
    io_fairshare.account_nas_open(tiny)
    assert io_fairshare._BUCKET.bytes_total() == mid
    io_fairshare.deactivate()


def test_index_metadata_imports_bootstrap():
    src = (Path(__file__).resolve().parents[1] / ".." / "py" / "index_metadata.py").read_text(
        encoding="utf-8"
    )
    assert "import pipeline_io_bootstrap" in src
    assert "account_nas_open" in src
