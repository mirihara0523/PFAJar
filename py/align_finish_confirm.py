"""Confirm dialog before Napari Align Finish starts warping."""

from __future__ import annotations

from typing import Any

from qtpy.QtWidgets import QMessageBox


def confirm_align_finish(parent: Any = None) -> bool:
    """Return True when the user confirms Finish; Cancel is the safe default."""
    dialog = QMessageBox(parent)
    dialog.setIcon(QMessageBox.Icon.Warning)
    dialog.setWindowTitle("Finish alignment")
    dialog.setText("Finish alignment and warp all sections?")
    dialog.setInformativeText(
        "This saves your alignment session and starts warping every section "
        "(which can take a while). Choose Cancel to keep tuning in Napari."
    )
    dialog.setStandardButtons(
        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel
    )
    dialog.setDefaultButton(QMessageBox.StandardButton.Cancel)
    yes_btn = dialog.button(QMessageBox.StandardButton.Yes)
    if yes_btn is not None:
        yes_btn.setText("Finish")
    return dialog.exec() == QMessageBox.StandardButton.Yes
