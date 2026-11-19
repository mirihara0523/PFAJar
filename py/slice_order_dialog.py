"""Slice-order editor; no image, annotation, or AP data is modified here."""
from qtpy.QtCore import Qt
from qtpy.QtWidgets import (
    QAbstractItemView, QDialog, QDialogButtonBox, QHBoxLayout, QLabel,
    QListWidget, QListWidgetItem, QPushButton, QVBoxLayout,
)
from slice_index import natural_sort_key


class SliceOrderDialog(QDialog):
    def __init__(self, names, ap_positions, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Slice order")
        self.resize(600, 600)
        self.ap_positions = ap_positions
        layout = QVBoxLayout(self)
        note = QLabel("Drag sections or use Up / Down.\n"
                      "AP values and annotations stay with their original files.\n"
                      "Automatic AP extrapolation is paused after reordering; "
                      "Force AP step on Next remains available.\n"
                      "Viewer/Editor and Compare Adjacent use this order when reopened.")
        note.setWordWrap(True)
        layout.addWidget(note)
        self.sections = QListWidget()
        self.sections.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.sections.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        layout.addWidget(self.sections)
        self.set_names(names)
        for buttons in (
            [("Up", lambda: self.move_selected(-1)), ("Down", lambda: self.move_selected(1)),
             ("Reverse", lambda: self.set_names(list(reversed(self.names()))))],
            [("Filename order", lambda: self.set_names(sorted(self.names(), key=natural_sort_key))),
             ("AP ascending", self.sort_ap)],
        ):
            row = QHBoxLayout()
            for label, action in buttons:
                button = QPushButton(label)
                button.clicked.connect(action)
                row.addWidget(button)
            layout.addLayout(row)
        ap_note = QLabel("AP ascending uses current AP positions, including model estimates. "
                         "Use Reverse for descending order. New slices are appended on reopening.")
        ap_note.setWordWrap(True)
        layout.addWidget(ap_note)
        actions = QDialogButtonBox(QDialogButtonBox.StandardButton.Save |
                                  QDialogButtonBox.StandardButton.Cancel)
        actions.accepted.connect(self.accept)
        actions.rejected.connect(self.reject)
        layout.addWidget(actions)

    def names(self):
        return [self.sections.item(i).data(Qt.ItemDataRole.UserRole)
                for i in range(self.sections.count())]

    def set_names(self, names):
        selected = self.sections.currentItem()
        selected = selected.data(Qt.ItemDataRole.UserRole) if selected else None
        self.sections.clear()
        for name in names:
            ap = self.ap_positions.get(name)
            item = QListWidgetItem(f"{name}    AP: {ap if ap is not None else '—'}")
            item.setData(Qt.ItemDataRole.UserRole, name)
            self.sections.addItem(item)
            if name == selected:
                self.sections.setCurrentItem(item)

    def move_selected(self, delta):
        row = self.sections.currentRow()
        target = row + delta
        if row < 0 or target < 0 or target >= self.sections.count():
            return
        item = self.sections.takeItem(row)
        self.sections.insertItem(target, item)
        self.sections.setCurrentItem(item)

    def sort_ap(self):
        self.set_names(sorted(self.names(), key=lambda n: (
            self.ap_positions.get(n) is None, self.ap_positions.get(n, 0),
            natural_sort_key(n),
        )))
