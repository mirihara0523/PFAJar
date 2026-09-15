"""Count pairs predictions to annotations by slice stem, not sorted index."""

from __future__ import annotations

import sys
from pathlib import Path

py_dir = Path(__file__).resolve().parents[2] / "py"
sys.path.insert(0, str(py_dir))

from slice_index import pair_prediction_annotation_pkls  # noqa: E402


def test_pair_by_stem_ignores_sort_order() -> None:
    """Annotations and predictions in different lexicographic order still pair correctly."""
    preds = [
        "Predictions_M528_s099.pkl",
        "Predictions_M528_s061.pkl",
    ]
    annos = [
        "Annotation_M528_s061.pkl",
        "Annotation_M528_s099.pkl",
    ]
    paired = pair_prediction_annotation_pkls(preds, annos)
    assert paired == [
        ("Predictions_M528_s061.pkl", "Annotation_M528_s061.pkl"),
        ("Predictions_M528_s099.pkl", "Annotation_M528_s099.pkl"),
    ]


def test_pair_skips_unmatched_prediction() -> None:
    paired = pair_prediction_annotation_pkls(
        ["Predictions_M528_s061.pkl", "Predictions_M528_s099.pkl"],
        ["Annotation_M528_s061.pkl"],
    )
    assert paired == [
        ("Predictions_M528_s061.pkl", "Annotation_M528_s061.pkl"),
    ]


def test_pair_plain_annotation_stem() -> None:
    paired = pair_prediction_annotation_pkls(
        ["Predictions_M528_s062.pkl"],
        ["M528_s062.pkl"],
    )
    assert paired == [
        ("Predictions_M528_s062.pkl", "M528_s062.pkl"),
    ]
