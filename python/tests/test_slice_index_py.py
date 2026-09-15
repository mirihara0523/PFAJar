"""Tests for py/slice_index.py output helpers."""

from __future__ import annotations

import sys
from pathlib import Path

py_dir = Path(__file__).resolve().parents[2] / "py"
sys.path.insert(0, str(py_dir))

from slice_index import (  # noqa: E402
    filter_by_outputs,
    load_slice_list,
    output_exists_align,
    output_exists_intensity,
    slice_id_allowed,
    index_annotation_pkls,
    slice_stem_from_annotation_pkl,
    slice_stem_from_prediction_pkl,
)


def test_output_exists_intensity(tmp_path: Path) -> None:
    pkls = tmp_path / "pkls"
    pkls.mkdir()
    (pkls / "M528_s061_VISp.pkl").write_bytes(b"x")
    assert output_exists_intensity(pkls, "M528_s061")
    assert not output_exists_intensity(pkls, "M528_s099")


def test_filter_by_outputs_merge(tmp_path: Path) -> None:
    out = tmp_path / "slices"
    out.mkdir()
    (out / "Annotation_M528_s061.pkl").write_bytes(b"x")
    to_process, skipped = filter_by_outputs(
        ["M528_s061", "M528_s062"], out, "align", "merge"
    )
    assert to_process == ["M528_s062"]
    assert skipped == ["M528_s061"]


def test_filter_by_outputs_skip_matches_merge(tmp_path: Path) -> None:
    out = tmp_path / "slices"
    out.mkdir()
    (out / "Annotation_M528_s061.pkl").write_bytes(b"x")
    to_process, skipped = filter_by_outputs(
        ["M528_s061", "M528_s062"], out, "align", "skip"
    )
    assert to_process == ["M528_s062"]
    assert skipped == ["M528_s061"]


def test_filter_by_outputs_overwrite(tmp_path: Path) -> None:
    out = tmp_path / "slices"
    out.mkdir()
    (out / "Annotation_M528_s061.pkl").write_bytes(b"x")
    to_process, skipped = filter_by_outputs(
        ["M528_s061", "M528_s062"], out, "align", "overwrite"
    )
    assert to_process == ["M528_s061", "M528_s062"]
    assert skipped == []


def test_output_exists_align_annotations_prefix(tmp_path: Path) -> None:
    out = tmp_path / "slices"
    out.mkdir()
    (out / "annotations_M528_s061.pkl").write_bytes(b"x")
    assert output_exists_align(out, "M528_s061")
    assert not output_exists_align(out, "M528_s099")


def test_load_slice_list_dict(tmp_path: Path) -> None:
    path = tmp_path / "run_slice_list.json"
    path.write_text('{"slice_ids": ["M528_s061", "M528_s062"]}', encoding="utf-8")
    allowed = load_slice_list(path)
    assert allowed == {"M528_s061", "M528_s062"}
    assert slice_id_allowed("M528_s061", allowed)
    assert not slice_id_allowed("M528_s099", allowed)


def test_load_slice_list_missing_returns_none() -> None:
    assert load_slice_list(None) is None
    assert load_slice_list("/nonexistent/run_slice_list.json") is None


def test_slice_stem_prediction_and_annotation() -> None:
    assert slice_stem_from_prediction_pkl("Predictions_M528_s061.pkl") == "M528_s061"
    assert slice_stem_from_annotation_pkl("Annotation_M528_s061.pkl") == "M528_s061"
    assert slice_stem_from_annotation_pkl("annotations_M528_s062.pkl") == "M528_s062"
    assert slice_stem_from_annotation_pkl("M528_s063.pkl") == "M528_s063"


def test_index_annotation_pkls_prefers_first() -> None:
    m = index_annotation_pkls(
        ["Annotation_B.pkl", "Annotation_A.pkl", "Predictions_A.pkl"]
    )
    assert m["B"] == "Annotation_B.pkl"
    assert m["A"] == "Annotation_A.pkl"
    assert "Predictions_A.pkl" not in str(m.values())
