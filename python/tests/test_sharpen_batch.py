"""Tests for py/sharpen.py batch run (sequential, no ProcessPoolExecutor)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pytest
import tifffile

REPO_PY = Path(__file__).resolve().parents[2] / "py"
sys.path.insert(0, str(REPO_PY))

pytest.importorskip("cv2")

import sharpen  # noqa: E402


def _batch_args(input_dir: Path, output_dir: Path, **overrides) -> argparse.Namespace:
    defaults = {
        "config": "",
        "input": str(input_dir),
        "output": str(output_dir),
        "radius": "3",
        "amount": "2",
        "equalize": False,
        "slice_list": "",
        "preview": False,
        "image": "",
        "x": "0",
        "y": "0",
        "w": "512",
        "h": "512",
        "preview_dir": "",
    }
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


def _write_uint8_tiff(path: Path, shape: tuple[int, int] = (32, 32)) -> None:
    img = np.random.randint(20, 200, shape, dtype=np.uint8)
    tifffile.imwrite(str(path), img)


def test_sharpen_image_uses_tiled_for_large_arrays(monkeypatch) -> None:
    monkeypatch.setattr(sharpen, "TILED_SHARPEN_PIXEL_THRESHOLD", 1000)
    monkeypatch.setattr(sharpen, "TILED_SHARPEN_TILE", 64)
    monkeypatch.setattr(sharpen, "TILED_SHARPEN_PAD", 8)
    img = np.random.randint(10, 200, (48, 48), dtype=np.uint8)
    out = sharpen.sharpen_image(img, radius=2.0, amount=1.5, equalize=False)
    assert out.shape == img.shape
    assert out.dtype == np.uint8


def test_uint16_input_scales_not_truncates() -> None:
    h, w = 64, 64
    ramp = (np.arange(h * w, dtype=np.uint16).reshape(h, w) % 256) * 257
    out = sharpen.sharpen_image(ramp, radius=2.0, amount=1.5, equalize=False)
    assert out.dtype == np.uint16
    assert int(out.max()) > 1000


def test_large_uint16_tiled_output_preserves_dtype(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sharpen, "TILED_SHARPEN_PIXEL_THRESHOLD", 1000)
    monkeypatch.setattr(sharpen, "TILED_SHARPEN_TILE", 64)
    monkeypatch.setattr(sharpen, "TILED_SHARPEN_PAD", 8)

    in_dir = tmp_path / "in"
    out_dir = tmp_path / "out"
    in_dir.mkdir()
    h, w = 48, 48
    uint16 = (np.random.randint(0, 256, (h, w), dtype=np.uint16) * 257).astype(np.uint16)
    tifffile.imwrite(str(in_dir / "big.tif"), uint16)

    rc = sharpen.run_batch(_batch_args(in_dir, out_dir))
    assert rc == 0
    written = tifffile.imread(str(out_dir / "big.tif"))
    assert written.dtype == np.uint16
    assert int(written.max()) > 1000


def test_batch_writes_two_files(tmp_path: Path) -> None:
    in_dir = tmp_path / "in"
    out_dir = tmp_path / "out"
    in_dir.mkdir()
    _write_uint8_tiff(in_dir / "s001.tif")
    _write_uint8_tiff(in_dir / "s002.tif")

    rc = sharpen.run_batch(_batch_args(in_dir, out_dir))
    assert rc == 0
    assert (out_dir / "s001.tif").is_file()
    assert (out_dir / "s002.tif").is_file()
    manifest = json.loads((out_dir / "run_manifest.json").read_text(encoding="utf-8"))
    assert manifest["step"] == "sharpen"
    assert set(manifest["input_files"]) == {"s001.tif", "s002.tif"}


def test_batch_empty_input_dir_exits_one(tmp_path: Path) -> None:
    in_dir = tmp_path / "in"
    out_dir = tmp_path / "out"
    in_dir.mkdir()

    rc = sharpen.run_batch(_batch_args(in_dir, out_dir))
    assert rc == 1
    assert not (out_dir / "run_manifest.json").exists()


def test_batch_partial_success_when_one_file_unreadable(tmp_path: Path, capsys) -> None:
    in_dir = tmp_path / "in"
    out_dir = tmp_path / "out"
    in_dir.mkdir()
    _write_uint8_tiff(in_dir / "good.tif")
    (in_dir / "bad.tif").write_bytes(b"not a tiff")

    rc = sharpen.run_batch(_batch_args(in_dir, out_dir))
    assert rc == 0
    assert (out_dir / "good.tif").is_file()
    assert not (out_dir / "bad.tif").exists()
    captured = capsys.readouterr()
    assert "LOG: Failed bad.tif" in captured.out
    manifest = json.loads((out_dir / "run_manifest.json").read_text(encoding="utf-8"))
    assert manifest["input_files"] == ["good.tif"]


def test_batch_done_is_last_line(tmp_path: Path, capsys) -> None:
    in_dir = tmp_path / "in"
    out_dir = tmp_path / "out"
    in_dir.mkdir()
    _write_uint8_tiff(in_dir / "s001.tif")

    rc = sharpen.run_batch(_batch_args(in_dir, out_dir))
    assert rc == 0
    captured = capsys.readouterr()
    out_lines = [line.strip() for line in captured.out.splitlines() if line.strip()]
    assert out_lines[-1] == "Done!"
    assert (out_dir / "run_manifest.json").is_file()


def test_batch_manifest_failure_still_exits_zero(tmp_path: Path, monkeypatch, capsys) -> None:
    in_dir = tmp_path / "in"
    out_dir = tmp_path / "out"
    in_dir.mkdir()
    _write_uint8_tiff(in_dir / "s001.tif")

    def _boom(*_args, **_kwargs):
        raise OSError("manifest write failed")

    monkeypatch.setattr("run_manifest.write_run_manifest", _boom)

    rc = sharpen.run_batch(_batch_args(in_dir, out_dir))
    assert rc == 0
    assert (out_dir / "s001.tif").is_file()
    captured = capsys.readouterr()
    assert "LOG: sharpen_manifest_failed" in captured.out
    assert captured.out.strip().endswith("Done!")


def test_batch_all_fail_exits_one(tmp_path: Path, capsys) -> None:
    in_dir = tmp_path / "in"
    out_dir = tmp_path / "out"
    in_dir.mkdir()
    (in_dir / "bad1.tif").write_bytes(b"x")
    (in_dir / "bad2.tif").write_bytes(b"y")

    rc = sharpen.run_batch(_batch_args(in_dir, out_dir))
    assert rc == 1
    assert not (out_dir / "run_manifest.json").exists()
    captured = capsys.readouterr()
    assert "SHARPEN_NO_OUTPUT: 0 of 2 files written." in captured.out
