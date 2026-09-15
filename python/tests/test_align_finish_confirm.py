"""Tests for Align Finish confirmation dialog (accidental Finish guard)."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

REPO_PY = Path(__file__).resolve().parents[2] / "py"
sys.path.insert(0, str(REPO_PY))

from align_finish_confirm import confirm_align_finish  # noqa: E402


@pytest.fixture
def qapp():
    from qtpy.QtWidgets import QApplication

    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def test_confirm_align_finish_cancel_returns_false(qapp, monkeypatch):
    from qtpy.QtWidgets import QMessageBox

    monkeypatch.setattr(
        QMessageBox, "exec", lambda self: QMessageBox.StandardButton.Cancel
    )
    assert confirm_align_finish(None) is False


def test_confirm_align_finish_yes_returns_true(qapp, monkeypatch):
    from qtpy.QtWidgets import QMessageBox

    seen = {}

    def fake_exec(self):
        yes_btn = self.button(QMessageBox.StandardButton.Yes)
        cancel_btn = self.button(QMessageBox.StandardButton.Cancel)
        seen["yes_text"] = yes_btn.text() if yes_btn is not None else None
        seen["default_is_cancel"] = self.defaultButton() is cancel_btn
        return QMessageBox.StandardButton.Yes

    monkeypatch.setattr(QMessageBox, "exec", fake_exec)
    assert confirm_align_finish(None) is True
    assert seen["yes_text"] == "Finish"
    assert seen["default_is_cancel"] is True


def test_finish_cancel_does_not_save_or_mark_finished():
    """Mirrors AlignmentController.finish early-exit when confirm is False."""
    ctrl = SimpleNamespace(_session_finished=False)
    ctrl._confirm_finish = lambda: False
    ctrl.save_alignment = MagicMock()

    def finish():
        if not ctrl._confirm_finish():
            return
        ctrl._session_finished = True
        ctrl.save_alignment()

    finish()
    assert ctrl._session_finished is False
    ctrl.save_alignment.assert_not_called()


def test_finish_confirm_proceeds_to_save():
    """Mirrors AlignmentController.finish when confirm is True."""
    ctrl = SimpleNamespace(_session_finished=False)
    ctrl._confirm_finish = lambda: True
    ctrl.save_alignment = MagicMock()

    def finish():
        if not ctrl._confirm_finish():
            return
        ctrl._session_finished = True
        ctrl.save_alignment()

    finish()
    assert ctrl._session_finished is True
    ctrl.save_alignment.assert_called_once()
