"""Tests for py/qt_window_utils.py (window focus helpers)."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_REPO_ROOT = Path(__file__).resolve().parents[2]
_PY_DIR = _REPO_ROOT / "py"
if str(_PY_DIR) not in sys.path:
    sys.path.insert(0, str(_PY_DIR))

import qt_window_utils  # noqa: E402  (after sys.path patch)


@pytest.fixture
def mock_widget():
    widget = MagicMock()
    widget.isVisible.return_value = False
    widget.winId.return_value = 1234
    return widget


def test_raise_and_activate_calls_show_raise_activate(mock_widget, monkeypatch):
    # Avoid real QTimer in tests: monkeypatch the singleShot to no-op
    timer_mock = MagicMock()
    monkeypatch.setattr(
        "qt_window_utils.QTimer", timer_mock, raising=False
    )
    qt_window_utils.raise_and_activate(mock_widget)
    mock_widget.show.assert_called_once()
    mock_widget.raise_.assert_called_once()
    mock_widget.activateWindow.assert_called_once()


def test_raise_and_activate_noop_for_none(monkeypatch):
    # Should not raise when widget is None
    qt_window_utils.raise_and_activate(None)


def test_raise_and_activate_skips_show_when_already_visible(monkeypatch):
    widget = MagicMock()
    widget.isVisible.return_value = True
    qt_window_utils.raise_and_activate(widget)
    widget.show.assert_not_called()
    widget.raise_.assert_called_once()
    widget.activateWindow.assert_called_once()


def test_raise_and_activate_swallows_exceptions(monkeypatch):
    widget = MagicMock()
    widget.isVisible.return_value = False
    widget.raise_.side_effect = RuntimeError("boom")
    widget.activateWindow.side_effect = RuntimeError("boom2")
    # Should not propagate
    qt_window_utils.raise_and_activate(widget)


def test_raise_and_activate_napari_resolves_qt_window(monkeypatch):
    qt_window = MagicMock()
    qt_window.isVisible.return_value = False
    viewer = MagicMock()
    viewer.window._qt_window = qt_window

    qt_window_utils.raise_and_activate_napari(viewer)
    qt_window.show.assert_called_once()
    qt_window.raise_.assert_called_once()
    qt_window.activateWindow.assert_called_once()


def test_raise_and_activate_napari_falls_back_to_window(monkeypatch):
    # MagicMock with spec=[] auto-rejects unknown attributes (so _qt_window is absent),
    # exercising the fallback to ``viewer.window`` itself.
    window_mock = MagicMock(
        spec=["isVisible", "show", "raise_", "activateWindow", "winId"]
    )
    window_mock.isVisible.return_value = False
    window_mock.winId.return_value = 0
    viewer = MagicMock()
    viewer.window = window_mock

    qt_window_utils.raise_and_activate_napari(viewer)
    window_mock.show.assert_called_once()
    window_mock.raise_.assert_called_once()
    window_mock.activateWindow.assert_called_once()


def test_raise_and_activate_napari_noop_for_none():
    qt_window_utils.raise_and_activate_napari(None)
