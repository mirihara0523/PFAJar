"""Confirm dialog before Napari Align Finish starts warping."""

from __future__ import annotations

from typing import Any

from qtpy.QtWidgets import (
    QButtonGroup,
    QDialog,
    QDialogButtonBox,
    QLabel,
    QMessageBox,
    QRadioButton,
    QVBoxLayout,
)


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


def choose_registration_quality(parent: Any = None, default: str = "standard") -> str | None:
    """Ask which registration quality Finish should warp every section with.

    Returns "standard", "precise", or None if the user cancelled -- callers
    should abort Finish on None, mirroring confirm_align_finish's
    Cancel-is-safe default.
    """
    dialog = QDialog(parent)
    dialog.setWindowTitle("Registration method")
    layout = QVBoxLayout(dialog)

    intro = QLabel(
        "Choose how each section is registered to the atlas during warping."
    )
    intro.setWordWrap(True)
    layout.addWidget(intro)

    standard_radio = QRadioButton(
        "Standard (360\u00d7360, 5\u00d75 B-spline mesh) \u2014 current default, fastest"
    )
    precise_radio = QRadioButton(
        "Precise (512\u00d7512, 8\u00d78 B-spline mesh) \u2014 finer internal-structure "
        "detail, slower per section"
    )
    group = QButtonGroup(dialog)
    group.addButton(standard_radio)
    group.addButton(precise_radio)
    if default == "precise":
        precise_radio.setChecked(True)
    else:
        standard_radio.setChecked(True)
    layout.addWidget(standard_radio)
    layout.addWidget(precise_radio)

    note = QLabel(
        "Precise mode roughly doubles per-section registration time (more for "
        "the whole batch). Consider it when the atlas outline matches the "
        "tissue silhouette but internal structures (ventricles, layer bands, "
        "etc.) look under-deformed."
    )
    note.setWordWrap(True)
    note.setStyleSheet("color: #888888;")
    layout.addWidget(note)

    buttons = QDialogButtonBox(
        QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
    )
    buttons.accepted.connect(dialog.accept)
    buttons.rejected.connect(dialog.reject)
    layout.addWidget(buttons)

    if dialog.exec() != QDialog.DialogCode.Accepted:
        return None
    return "precise" if precise_radio.isChecked() else "standard"
