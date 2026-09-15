"""Tests for detection QC histogram helpers."""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_PY_DIR = _REPO_ROOT / "py"
if str(_PY_DIR) not in sys.path:
    sys.path.insert(0, str(_PY_DIR))

from detect_qc import (
    DetectQcCollector,
    LEGACY_RUN_QC_FILES,
    RUN_QC_FILES,
    bbox_area,
    bbox_intensity_p90,
    bbox_intensity_peak,
    build_summary_payload,
    cleanup_detect_qc_artifacts,
    write_run_histograms,
)
from detect_qc_analysis import (
    analyze_detection_qc,
    estimate_intensity_threshold,
    filter_objects_by_intensity,
    suggest_detection_params,
    DetectionRecord,
)


class _Score:
    def __init__(self, value: float):
        self.value = value


class _BBox:
    def __init__(self, xyxy):
        self._xyxy = xyxy

    def to_xyxy(self):
        return self._xyxy


class _Obj:
    def __init__(self, xyxy, score: float):
        self.bbox = _BBox(xyxy)
        self.score = _Score(score)


def test_bbox_metrics():
    assert bbox_area([0, 0, 10, 4]) == 40.0


def test_bbox_intensity_on_synthetic_patch():
    gray = np.zeros((100, 100), dtype=np.uint8)
    gray[20:40, 20:40] = 200
    assert bbox_intensity_p90([20, 20, 40, 40], gray) == 200.0
    assert bbox_intensity_peak([20, 20, 40, 40], gray) == 200.0
    assert bbox_intensity_p90([0, 0, 0, 0], gray) is None


def test_collector_aggregates_run_and_slice():
    gray = np.full((64, 64), 128, dtype=np.uint8)
    gray[10:30, 10:30] = 220
    collector = DetectQcCollector()
    raw = [_Obj([0, 0, 12, 8], 0.42), _Obj([10, 10, 30, 30], 0.88)]
    final = [_Obj([10, 10, 30, 30], 0.88)]
    collector.add_slice_pass(
        "M528_s001",
        raw,
        final,
        [0.31, 0.72],
        [(0.31, 180.0), (0.72, 210.0)],
        gray,
    )
    assert len(collector.raw_run.confidence) == 2
    assert len(collector.run.confidence) == 1
    assert collector.pre_ecc_run.eccentricity == [0.31, 0.72]
    assert collector.pre_ecc_records == [(0.31, 180.0), (0.72, 210.0)]
    assert collector.raw_run.intensity[1] == 220.0
    assert "M528_s001" in collector.slices


def test_cleanup_detect_qc_artifacts(tmp_path: Path):
    (tmp_path / "detect_qc_confidence.png").write_bytes(b"png")
    (tmp_path / LEGACY_RUN_QC_FILES[0]).write_bytes(b"png")
    (tmp_path / "detect_qc_summary.json").write_text("{}", encoding="utf-8")
    slice_dir = tmp_path / "detect_qc_slices" / "M528_s001"
    slice_dir.mkdir(parents=True)
    (slice_dir / "confidence.png").write_bytes(b"png")
    removed = cleanup_detect_qc_artifacts(tmp_path)
    assert "detect_qc_confidence.png" in removed
    assert LEGACY_RUN_QC_FILES[0] in removed
    assert "detect_qc_summary.json" in removed
    assert not (tmp_path / "detect_qc_slices").exists()


def test_write_run_histograms_smoke(tmp_path: Path):
    gray = np.full((80, 80), 100, dtype=np.uint8)
    gray[0:20, 0:20] = 240
    gray[2:8, 2:8] = 50
    collector = DetectQcCollector()
    collector.add_slice_pass(
        "M528_s001",
        [_Obj([0, 0, 20, 10], 0.55), _Obj([2, 2, 8, 8], 0.91)],
        [_Obj([0, 0, 20, 10], 0.55)],
        [0.4, 0.85],
        [(0.4, 200.0), (0.85, 80.0)],
        gray,
    )
    result = write_run_histograms(
        collector,
        tmp_path,
        {"confidence": 0.5, "area_px2": 200, "eccentricity": 0.5},
        per_slice_enabled=True,
    )
    assert RUN_QC_FILES[1] == "detect_qc_area_px2.png"
    for rel in result["run_files"]:
        path = tmp_path / rel
        assert path.is_file()
        assert path.stat().st_size > 0
    assert (tmp_path / "detect_qc_summary.json").is_file()
    assert "M528_s001" in result["slice_files"]
    summary = build_summary_payload(
        collector,
        {"confidence": 0.5, "area_px2": 200, "eccentricity": 0.5},
        per_slice_enabled=True,
        run_files=result["run_files"],
        slice_files=result["slice_files"],
    )
    assert summary["run"]["final"]["confidence"]["count"] == 1
    assert summary["run"]["raw"]["intensity_p90"]["count"] == 2
    assert summary["run"]["final"]["intensity_peak"]["count"] == 1


def test_estimate_intensity_threshold_bimodal():
    rng = np.random.default_rng(0)
    low = rng.normal(35, 4, 200)
    high = rng.normal(110, 12, 180)
    vals = np.clip(np.concatenate([low, high]), 0, 255).tolist()
    info = estimate_intensity_threshold(vals)
    assert info["bimodal"] is True
    assert info["intensity_threshold_estimate"] is not None
    assert 40 <= info["intensity_threshold_estimate"] <= 65


def test_estimate_intensity_threshold_unimodal():
    rng = np.random.default_rng(1)
    vals = rng.normal(100, 5, 80).tolist()
    info = estimate_intensity_threshold(vals)
    assert info["bimodal"] is False


def test_suggest_detection_params_prefers_high_intensity():
    threshold = 50.0
    records = [
        DetectionRecord(0.9, 400, 120, 0.8),
        DetectionRecord(0.85, 350, 115, 0.75),
        DetectionRecord(0.6, 300, 35, 0.7),
        DetectionRecord(0.55, 280, 32, 0.65),
    ]
    suggestions = suggest_detection_params(
        records, threshold, {"confidence": 0.5, "area_px2": 200, "eccentricity": 0.2}
    )
    assert suggestions == {"intensity_min": 50}
    assert "confidence" not in suggestions
    assert "area" not in suggestions
    assert "eccentricity" not in suggestions


def test_filter_objects_by_intensity():
    gray = np.zeros((50, 50), dtype=np.uint8)
    gray[5:15, 5:15] = 30
    gray[25:35, 25:35] = 200
    dim = _Obj([5, 5, 15, 15], 0.7)
    bright = _Obj([25, 25, 35, 35], 0.8)
    kept, removed = filter_objects_by_intensity([dim, bright], gray, 50)
    assert removed == 1
    assert len(kept) == 1


def test_filter_objects_by_intensity_disabled():
    objs = [_Obj([0, 0, 10, 10], 0.5)]
    kept, removed = filter_objects_by_intensity(objs, None, 0)
    assert len(kept) == 1
    assert removed == 0


def test_write_run_histograms_includes_analysis(tmp_path: Path):
    gray = np.full((80, 80), 100, dtype=np.uint8)
    collector = DetectQcCollector()
    rng = np.random.default_rng(2)
    raw_objs = []
    final_objs = []
    pre_ecc = []
    pre_rec = []
    for i in range(40):
        low = i < 22
        inten = float(rng.normal(35 if low else 110, 3))
        conf = float(rng.uniform(0.5, 0.9))
        area = float(rng.uniform(250, 600))
        x = int(i * 2) % 60
        raw_objs.append(_Obj([x, x, x + 12, x + 10], conf))
        pre_ecc.append(0.7)
        pre_rec.append((0.7, inten))
        if not low or conf > 0.55:
            final_objs.append(_Obj([x, x, x + 12, x + 10], conf))
    collector.add_slice_pass(
        "M528_s001", raw_objs, final_objs, pre_ecc, pre_rec, gray
    )
    result = write_run_histograms(
        collector,
        tmp_path,
        {"confidence": 0.5, "area_px2": 200, "eccentricity": 0.2, "intensity_min": 0},
    )
    assert "analysis" in result
    import json

    summary = json.loads((tmp_path / "detect_qc_summary.json").read_text(encoding="utf-8"))
    assert "analysis" in summary
    assert "summary_lines" in summary["analysis"]
    sug = summary["analysis"].get("suggestions") or {}
    assert set(sug.keys()) <= {"intensity_min"}
