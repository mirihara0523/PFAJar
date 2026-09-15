"""Tests for SAHI get_sliced_prediction kwargs compatibility in find_neurons."""

import inspect
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_PY_DIR = _REPO_ROOT / "py"
if str(_PY_DIR) not in sys.path:
    sys.path.insert(0, str(_PY_DIR))

import find_neurons  # noqa: E402


def _signature_with_params(*param_names):
    return inspect.Signature(
        [
            inspect.Parameter(name, inspect.Parameter.KEYWORD_ONLY, default=None)
            for name in param_names
        ]
    )


OLD_SAHI_PARAMS = (
    "image",
    "detection_model",
    "slice_height",
    "slice_width",
    "overlap_height_ratio",
    "overlap_width_ratio",
    "verbose",
)

NEW_SAHI_PARAMS = OLD_SAHI_PARAMS + ("progress_bar", "progress_callback")


@pytest.fixture
def dummy_inputs():
    image = __import__("numpy").zeros((64, 64, 3), dtype="uint8")
    model = MagicMock()
    return image, model


def test_call_omits_progress_kwargs_on_old_sahi_signature(dummy_inputs):
    image, model = dummy_inputs
    with patch.object(
        find_neurons.inspect,
        "signature",
        return_value=_signature_with_params(*OLD_SAHI_PARAMS),
    ):
        with patch.object(
            find_neurons,
            "get_sliced_prediction",
            return_value=MagicMock(object_prediction_list=[]),
        ) as mock_pred:
            find_neurons._call_get_sliced_prediction(image, model, 512, "slice.tif")
            kwargs = mock_pred.call_args.kwargs
            assert "progress_bar" not in kwargs
            assert "progress_callback" not in kwargs
            assert kwargs["slice_height"] == 512
            assert kwargs["verbose"] == 1


def test_call_passes_progress_kwargs_on_new_sahi_signature(dummy_inputs):
    image, model = dummy_inputs
    with patch.object(
        find_neurons.inspect,
        "signature",
        return_value=_signature_with_params(*NEW_SAHI_PARAMS),
    ):
        with patch.object(
            find_neurons,
            "get_sliced_prediction",
            return_value=MagicMock(object_prediction_list=[]),
        ) as mock_pred:
            find_neurons._call_get_sliced_prediction(image, model, 512, "slice.tif")
            kwargs = mock_pred.call_args.kwargs
            assert kwargs["progress_bar"] is False
            assert callable(kwargs["progress_callback"])
            assert kwargs["slice_width"] == 512


def test_progress_callback_emits_tile_lines(capsys):
    cb = find_neurons.make_tile_progress_printer("test.tif")
    cb(1, 10)
    cb(10, 10)
    out = capsys.readouterr().out
    assert "test.tif: tile 1/10" in out
    assert "test.tif: tile 10/10" in out
