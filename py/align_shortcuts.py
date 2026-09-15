"""Editable keyboard shortcuts for the atlas alignment viewer (map.py).

Stores user-customized key sequences in ``~/.masonjar/align_shortcuts.json`` and
provides a dialog to view/edit them. Kept separate from map.py to keep the viewer
module focused.
"""

from __future__ import annotations

import json
from pathlib import Path

from qtpy.QtCore import Qt
from qtpy.QtGui import QKeySequence
from qtpy.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHeaderView,
    QKeySequenceEdit,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)


def shortcuts_config_path() -> Path:
    return Path.home() / ".masonjar" / "align_shortcuts.json"


def load_shortcuts(defaults: dict) -> dict:
    """Merge saved shortcuts over the given defaults (unknown keys ignored)."""
    result = dict(defaults)
    try:
        p = shortcuts_config_path()
        if p.is_file():
            data = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                for k, v in data.items():
                    if k in result and isinstance(v, str):
                        result[k] = v
    except Exception:
        pass
    return result


def save_shortcuts(mapping: dict) -> None:
    try:
        p = shortcuts_config_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(mapping, indent=2), encoding="utf-8")
    except Exception:
        pass


class ShortcutsDialog(QDialog):
    """List shortcut-controllable actions with editable key sequences."""

    def __init__(self, parent, actions, current, defaults, on_apply):
        super().__init__(parent)
        self.setWindowTitle("Keyboard shortcuts")
        self._actions = list(actions)  # [(action_id, label)]
        self._defaults = dict(defaults)
        self._on_apply = on_apply

        layout = QVBoxLayout(self)
        layout.addWidget(
            QLabel(
                "Click a Shortcut cell and press the desired key combination.\n"
                "Clear a cell (Backspace) to disable that shortcut."
            )
        )

        self.table = QTableWidget(len(self._actions), 2, self)
        self.table.setHorizontalHeaderLabels(["Action", "Shortcut"])
        self.table.verticalHeader().setVisible(False)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)

        self._edits: dict = {}
        for row, (aid, label) in enumerate(self._actions):
            item = QTableWidgetItem(label)
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.table.setItem(row, 0, item)
            edit = QKeySequenceEdit(QKeySequence(current.get(aid, "")))
            self._edits[aid] = edit
            self.table.setCellWidget(row, 1, edit)
        layout.addWidget(self.table)

        buttons = QDialogButtonBox(self)
        reset_btn = QPushButton("Reset to defaults")
        buttons.addButton(reset_btn, QDialogButtonBox.ButtonRole.ResetRole)
        save_btn = buttons.addButton(QDialogButtonBox.StandardButton.Save)
        cancel_btn = buttons.addButton(QDialogButtonBox.StandardButton.Cancel)
        reset_btn.clicked.connect(self._reset_defaults)
        save_btn.clicked.connect(self._save)
        cancel_btn.clicked.connect(self.reject)
        layout.addWidget(buttons)
        self.resize(440, 340)

    def _reset_defaults(self) -> None:
        for aid, edit in self._edits.items():
            edit.setKeySequence(QKeySequence(self._defaults.get(aid, "")))

    def _collect(self) -> dict:
        out = {}
        for aid, edit in self._edits.items():
            out[aid] = edit.keySequence().toString()
        return out

    def _save(self) -> None:
        mapping = self._collect()
        try:
            if callable(self._on_apply):
                self._on_apply(mapping)
        except Exception:
            pass
        save_shortcuts(mapping)
        self.accept()
