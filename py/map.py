import os
import hashlib
import tempfile
import pipeline_io_bootstrap  # noqa: F401
import json
import threading
import time
import perf_log
from datetime import datetime, timezone
import numpy as np
import cv2
import pickle
from pathlib import Path
from demons import register_to_atlas
from align_tissue_mask import (
    WARP_MODE_CHOICES,
    WARP_MODE_DEFAULT,
    WARP_MODE_HYBRID,
    WARP_MODE_PER_ISLAND,
    WARP_MODE_REGION_DUAL,
    append_alignment_mask_log,
    keep_mask_stats,
    load_keep_mask,
    mask_is_trivial,
    resolve_bundle_root_from_dapi_dir,
    warp_mode_index,
)
from align_tissue_warp import warp_section_with_masks
from slice_atlas import slice_3d_volume, add_outlines, mask_slice_by_region
from align_tissue_layout import (
    crop_planar_for_hemisphere,
    detect_tissue_layout,
    parse_layout_mode,
)
from align_session import (
    apply_slice_tuning_from_controls,
    ap_extrapolation_locked,
    build_session_payload,
    compute_tuning_fingerprint,
    extrapolate_ap_positions,
    mark_session_completed,
    persist_session,
    recover_alignment_session,
    should_sync_controls_before_autosave,
    write_session_payload,
)

from model import TissuePredictor
import nrrd
import SimpleITK as sitk
import torch
from torchvision import transforms
import napari
import copy
import argparse
from qtpy.QtWidgets import (
    QApplication,
    QGridLayout,
    QGraphicsView,
    QGraphicsScene,
    QHBoxLayout,
    QPushButton,
    QProgressBar,
    QLabel,
    QComboBox,
    QCheckBox,
    QDoubleSpinBox,
    QVBoxLayout,
    QSizePolicy,
    QFrame,
    QWidget,
    QMainWindow,
    QInputDialog,
    QToolBar,
    QMessageBox,
)
from segment_anything import SamPredictor, sam_model_registry
from qtpy import QtCore, QtGui
from qtpy.QtCore import QTimer
from qt_image_utils import numpy_array_to_qimage
from qt_window_utils import (
    align_section_heading,
    arrange_right_docks_split,
    build_scroll_dock_panel,
    ensure_qt_dock_visible,
    hide_napari_layer_docks,
    resolve_napari_qt_window,
    show_napari_maximized_and_activate,
)
from align_finish_confirm import confirm_align_finish
import align_shortcuts

# Shortcut-controllable alignment actions: (handler-method name, menu/dialog label).
_ALIGN_SHORTCUT_ACTIONS = [
    ("previous_section", "Previous section"),
    ("next_section", "Next section"),
    ("prompt_goto_section", "Go to section…"),
    ("flag_current_section", "Flag section…"),
    ("apply_layout_to_all", "Apply layout to all sections"),
    ("finish", "Finish alignment"),
]
_ALIGN_SHORTCUT_DEFAULTS = {
    "previous_section": "Ctrl+Left",
    "next_section": "Ctrl+Right",
    "prompt_goto_section": "Ctrl+G",
    "flag_current_section": "Ctrl+F",
    "apply_layout_to_all": "",
    "finish": "Ctrl+Return",
}
# Toolbar/panel button attribute for each shortcut action, so hovering the button
# reveals its current shortcut in a tooltip.
_ALIGN_SHORTCUT_BUTTONS = {
    "previous_section": "previous_button",
    "next_section": "next_button",
    "prompt_goto_section": "goto_button",
    "flag_current_section": "flag_section_button",
    "apply_layout_to_all": "apply_layout_all_button",
    "finish": "finish_button",
}
from czi_common import (
    emit_log,
    emit_progress_phase,
    emit_result,
    natural_sort_filenames,
)


class AtlasSidebarWidthFilter(QtCore.QObject):
    """Persist a user-resized Alignment sidebar without resizing on launch."""

    def __init__(self, owner, dock):
        super().__init__(dock)
        self.owner = owner
        self.dock = dock

    def eventFilter(self, source, event):
        if (
            source is self.dock
            and event.type() == QtCore.QEvent.Type.Resize
            and self.owner._options_width_ready
            and self.dock.isVisible()
            and not self.dock.isFloating()
            and self.dock.width() > 0
        ):
            width = self.dock.width()
            self.owner._saved_options_dock_width = width
            self.owner._options_settings.setValue(
                "alignment/optionsDockWidth", width
            )
        return False


class DraggableSidebarSectionHeader(QLabel):
    """A clickable section heading that also starts an internal reorder drag."""

    MIME_TYPE = "application/x-masonjar-alignment-sidebar-section"
    toggleRequested = QtCore.Signal(str)

    def __init__(self, section_id: str, title: str, parent=None):
        super().__init__(title, parent)
        self.section_id = section_id
        self._press_pos = None
        self._drag_started = False
        font = self.font()
        font.setBold(True)
        self.setFont(font)
        self.setCursor(QtCore.Qt.CursorShape.OpenHandCursor)
        self.setToolTip("Drag to reorder this sidebar section.")

    def mousePressEvent(self, event):
        if event.button() == QtCore.Qt.MouseButton.LeftButton:
            self._press_pos = event.pos()
            self._drag_started = False
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if (
            self._press_pos is not None
            and event.buttons() & QtCore.Qt.MouseButton.LeftButton
            and (event.pos() - self._press_pos).manhattanLength()
            >= QApplication.startDragDistance()
        ):
            drag = QtGui.QDrag(self)
            mime = QtCore.QMimeData()
            mime.setData(self.MIME_TYPE, self.section_id.encode("utf-8"))
            drag.setMimeData(mime)
            drag.exec(QtCore.Qt.DropAction.MoveAction)
            self._drag_started = True
            self._press_pos = None
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if (
            event.button() == QtCore.Qt.MouseButton.LeftButton
            and self._press_pos is not None
            and not self._drag_started
        ):
            self.toggleRequested.emit(self.section_id)
        self._press_pos = None
        self._drag_started = False
        super().mouseReleaseEvent(event)


class ReorderableSidebarSections(QWidget):
    """Vertical, drag-reorderable sidebar sections with stable identifiers."""

    orderChanged = QtCore.Signal(list)
    collapsedChanged = QtCore.Signal(list)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self._sections = {}
        self._separators = {}
        self._contents = {}
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(8)

    def add_section(self, section_id: str, title: str, widgets):
        section = QWidget(self)
        section_layout = QVBoxLayout(section)
        section_layout.setContentsMargins(0, 0, 0, 0)
        section_layout.setSpacing(6)
        separator = QFrame(section)
        separator.setFrameShape(QFrame.Shape.HLine)
        separator.setFrameShadow(QFrame.Shadow.Sunken)
        section_layout.addWidget(separator)
        header = DraggableSidebarSectionHeader(section_id, title, section)
        header.toggleRequested.connect(self.toggle_section)
        section_layout.addWidget(header)
        content = QWidget(section)
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(6)
        for widget in widgets:
            if widget is not None:
                content_layout.addWidget(widget)
        content.setLayout(content_layout)
        section_layout.addWidget(content)
        self._sections[section_id] = section
        self._separators[section_id] = separator
        self._contents[section_id] = content
        self._layout.addWidget(section)
        self._refresh_divider_visibility()

    def toggle_section(self, section_id: str):
        """Collapse or expand one major sidebar section without reordering it."""
        content = self._contents.get(section_id)
        if content is not None:
            # ``isHidden`` is stable even before the enclosing dock is shown;
            # ``isVisible`` would be false for every section at that point.
            content.setVisible(content.isHidden())
            self.collapsedChanged.emit(self.collapsed_sections())

    def collapsed_sections(self):
        """Return stable IDs for major sections whose body is collapsed."""
        return [
            section_id
            for section_id in self.order()
            if self._contents.get(section_id) is not None
            and self._contents[section_id].isHidden()
        ]

    def set_collapsed_sections(self, requested_sections):
        """Restore persisted collapse state without emitting a user-change signal."""
        requested = set(requested_sections or [])
        for section_id, content in self._contents.items():
            content.setVisible(section_id not in requested)

    def _refresh_divider_visibility(self):
        """Show dividers only between the currently ordered major sections."""
        for index, section_id in enumerate(self.order()):
            separator = self._separators.get(section_id)
            if separator is not None:
                separator.setVisible(index > 0)

    def order(self):
        result = []
        for index in range(self._layout.count()):
            widget = self._layout.itemAt(index).widget()
            for section_id, candidate in self._sections.items():
                if candidate is widget:
                    result.append(section_id)
                    break
        return result

    def set_order(self, requested_order):
        current = self.order()
        desired = [section_id for section_id in requested_order if section_id in self._sections]
        desired.extend(section_id for section_id in current if section_id not in desired)
        if desired == current:
            return
        for section_id in desired:
            self._layout.removeWidget(self._sections[section_id])
        for index, section_id in enumerate(desired):
            self._layout.insertWidget(index, self._sections[section_id])
        self._refresh_divider_visibility()

    def dragEnterEvent(self, event):
        if event.mimeData().hasFormat(DraggableSidebarSectionHeader.MIME_TYPE):
            event.acceptProposedAction()

    def dragMoveEvent(self, event):
        if event.mimeData().hasFormat(DraggableSidebarSectionHeader.MIME_TYPE):
            event.acceptProposedAction()

    def dropEvent(self, event):
        if not event.mimeData().hasFormat(DraggableSidebarSectionHeader.MIME_TYPE):
            event.ignore()
            return
        section_id = bytes(
            event.mimeData().data(DraggableSidebarSectionHeader.MIME_TYPE)
        ).decode("utf-8")
        dragged = self._sections.get(section_id)
        if dragged is None:
            event.ignore()
            return
        point = event.position().toPoint() if hasattr(event, "position") else event.pos()
        others = [
            current_id
            for current_id in self.order()
            if current_id != section_id
        ]
        insert_at = len(others)
        for index, current_id in enumerate(others):
            candidate = self._sections[current_id]
            if point.y() < candidate.geometry().center().y():
                insert_at = index
                break
        self._layout.removeWidget(dragged)
        self._layout.insertWidget(insert_at, dragged)
        self._refresh_divider_visibility()
        self.orderChanged.emit(self.order())
        event.acceptProposedAction()


class AtlasDamageMarker(QMainWindow):
    """Mark atlas regions missing on tissue due to damage (exclude from warp)."""

    closed = QtCore.Signal()

    INSTRUCTION = (
        "Attempt to mark the parts of this atlas section which are "
        "missing on your tissue due to damage."
    )

    def __init__(self, image):
        super().__init__()
        self.image = image
        self.mask_image = np.zeros_like(self.image)
        self._committed = False
        self.drawing = False
        self.brush_size = 3
        self.init_ui()

    def init_ui(self):
        self.setWindowTitle("Identify tissue damage")
        container = QWidget()
        ui_layout = QVBoxLayout()
        instruction = QLabel(self.INSTRUCTION)
        instruction.setWordWrap(True)
        instruction.setAlignment(QtCore.Qt.AlignmentFlag.AlignLeading)
        ui_layout.addWidget(instruction)

        self.img_view = QGraphicsView(self)
        self.img_view.setMouseTracking(True)
        self.img_view.viewport().installEventFilter(self)

        self.img_scene = QGraphicsScene(self)
        self.qimg = numpy_array_to_qimage(self.image)
        self.img_pixmap = QtGui.QPixmap.fromImage(self.qimg)
        self.img_scene.addPixmap(self.img_pixmap)
        self.img_view.setScene(self.img_scene)

        self.brush_size_slider = QSlider(QtCore.Qt.Horizontal, self)
        self.brush_size_slider_label = QLabel("Brush Size")
        self.brush_size_slider_label.setAlignment(QtCore.Qt.AlignmentFlag.AlignLeading)
        self.brush_size_slider.setMinimum(1)
        self.brush_size_slider.setMaximum(10)
        self.brush_size_slider.setValue(self.brush_size)
        self.brush_size_slider.valueChanged.connect(self.update_brush_size)

        self.save_button = QPushButton("Save", self)
        self.cancel_button = QPushButton("Cancel", self)
        self.save_button.clicked.connect(self.save_mask)
        self.cancel_button.clicked.connect(self.cancel_changes)

        ui_layout.addWidget(self.img_view)
        ui_layout.addWidget(self.brush_size_slider_label)
        ui_layout.addWidget(self.brush_size_slider)
        ui_layout.addWidget(self.save_button)
        ui_layout.addWidget(self.cancel_button)
        container.setLayout(ui_layout)
        self.setCentralWidget(container)

    def eventFilter(self, source, event):
        if source is self.img_view.viewport():
            if event.type() == QtCore.QEvent.MouseMove and self.drawing:
                self.draw_on_image(event.pos())
                return True
            elif (
                event.type() == QtCore.QEvent.MouseButtonPress
                and event.button() == QtCore.Qt.LeftButton
            ):
                self.drawing = True
                self.draw_on_image(event.pos())
                return True
            elif (
                event.type() == QtCore.QEvent.MouseButtonRelease
                and event.button() == QtCore.Qt.LeftButton
            ):
                self.drawing = False
                return True

        return super().eventFilter(source, event)

    def draw_on_image(self, qpoint):
        image_point = self.img_view.mapToScene(qpoint).toPoint()
        if image_point:
            points_to_draw = self.points_in_circle(
                (image_point.x(), image_point.y()), self.brush_size * 2
            )

            painter = QtGui.QPainter(self.img_pixmap)
            pen = QtGui.QPen(
                QtGui.QColor(255, 0, 255),
                self.brush_size * 2,
                cap=QtCore.Qt.RoundCap,
            )
            painter.setPen(pen)
            for pt in points_to_draw:
                try:
                    painter.drawPoint(pt[0], pt[1])
                    self.mask_image[pt[1], pt[0]] = 1
                except IndexError:
                    pass
            painter.end()

            self.img_scene.update()
            self.update_image()

    def points_in_circle(self, center, radius):
        points = []
        for x in range(center[0] - radius, center[0] + radius + 1):
            for y in range(center[1] - radius, center[1] + radius + 1):
                if (x - center[0]) ** 2 + (y - center[1]) ** 2 <= radius**2:
                    points.append((x, y))
        return points

    def update_image(self):
        self.img_scene.clear()
        self.img_scene.addPixmap(self.img_pixmap)
        self.img_view.setScene(self.img_scene)

    def update_brush_size(self, value):
        self.brush_size = value

    def save_mask(self):
        self.mask_image = self.mask_image.astype(np.uint8)
        kernel = np.ones((5, 5), np.uint8)
        self.mask_image = cv2.dilate(self.mask_image, kernel, iterations=5)
        self.mask_image = cv2.erode(self.mask_image, kernel, iterations=5)
        self.mask_image = np.logical_not(self.mask_image).astype(np.uint8)
        self._committed = True
        self.close()

    def cancel_changes(self):
        self.mask_image = np.zeros_like(self.image)
        self._committed = False
        self.close()

    def closeEvent(self, event):
        self.closed.emit()
        event.accept()


# Backward compatibility alias
ImageEraser = AtlasDamageMarker

class AtlasSlice:
    """
    Helper object to manage atlas slices

    Parameters:
        section_name (str): the filename of the slice
        ap_position (int): the ap position of the slice
        x_angle (float): the x angle of the slice
        y_angle (float): the y angle of the slice
        region (str): the region of the slice
        hemisphere (str): the hemisphere of the slice
    """

    def __init__(
        self, section_name, ap_position, x_angle, y_angle, region="A", hemisphere="W"
    ):
        self.section_name = section_name
        self.ap_position = int(ap_position)
        self.x_angle = float(x_angle)
        self.y_angle = float(y_angle)
        self.linked = True
        self.region = region
        self.hemisphere = hemisphere
        self.layout_confidence = 1.0
        self.layout_low_confidence = False
        self.layout_overridden = False
        self.image = None
        self.sam_image = None
        self.label = None
        self.damage_mask = None
        self.mask = None
        self.use_tissue_cleanup_mask = False
        self.tissue_mask_warp_mode = WARP_MODE_DEFAULT
        self.keep_mask_source = None
        self.eraser_window = None

    def layout_label(self) -> str:
        if self.hemisphere == "L":
            return "Left hemi"
        return "Whole brain"

    def slice_id(self) -> str:
        stem = ".".join(self.section_name.split(".")[:-1]) if "." in self.section_name else self.section_name
        return stem.split(".")[0]

    def set_damage_mask(self):
        """Open atlas damage marker for regions missing on tissue."""
        self.eraser_window = AtlasDamageMarker(self.image)
        self.eraser_window.show()
        self.eraser_window.closed.connect(self.on_damage_marker_exit)

    def on_damage_marker_exit(self):
        eraser = self.eraser_window
        self.eraser_window = None
        if eraser is not None and getattr(eraser, "_committed", False):
            keep_mask = eraser.mask_image
            if keep_mask is not None and keep_mask.size:
                damage = (1 - keep_mask.astype(np.uint8)).astype(np.uint8)
                if bool(np.any(damage)):
                    self.damage_mask = damage
                else:
                    self.damage_mask = None
        autosave_cb = getattr(self, "_autosave_cb", None)
        if autosave_cb is not None:
            autosave_cb()

    def set_mask(self):
        """Legacy alias for set_damage_mask."""
        self.set_damage_mask()

    def on_exit(self):
        self.on_damage_marker_exit()

    def set_slice(self, atlas, annotation):
        """
        Get the slice from the atlas and annotation

        Args:
            atlas (numpy.ndarray): the atlas
            annotation (numpy.ndarray): the annotation

        Returns:
            numpy.ndarray: the atlas slice
            numpy.ndarray: the annotation slice
        """
        # Skip recompute when nothing that affects the slice has changed
        # (e.g. revisiting a section). Any change to AP/angles/hemisphere
        # produces a different key and forces a fresh slice, so this cannot
        # show a stale slice. image/label are already stored on the instance,
        # so this adds no extra memory.
        cache_key = (
            self.ap_position,
            self.x_angle,
            self.y_angle,
            self.hemisphere,
        )
        if (
            getattr(self, "_slice_cache_key", None) == cache_key
            and getattr(self, "image", None) is not None
            and getattr(self, "label", None) is not None
        ):
            return

        self.image = slice_3d_volume(
            atlas, self.ap_position, self.x_angle, self.y_angle
        ).astype(np.uint8)
        self.label = slice_3d_volume(
            annotation, self.ap_position, self.x_angle, self.y_angle
        ).astype(np.uint32)
        self.image = crop_planar_for_hemisphere(self.image, self.hemisphere)
        self.label = crop_planar_for_hemisphere(self.label, self.hemisphere)
        self._slice_cache_key = cache_key

    def get_registered(
        self,
        tissue,
        structure_map_path,
        bundle_root=None,
        structure_map=None,
    ):
        """
        Runs multi-modal registration between this atlas slice and the provided tissue section.

        Returns:
            warped_labels, warped_atlas, color_label, warp_meta
        """
        slice_id = self.slice_id()
        damage_mask = self.damage_mask
        if damage_mask is None and self.mask is not None:
            damage_mask = (1 - self.mask.astype(np.uint8)).astype(np.uint8)

        keep_mask = None
        if self.use_tissue_cleanup_mask and bundle_root is not None:
            keep_mask, self.keep_mask_source = load_keep_mask(Path(bundle_root), slice_id)

        if self.use_tissue_cleanup_mask and keep_mask is not None:
            warped_labels, warped_atlas, color_label, warp_meta = warp_section_with_masks(
                tissue,
                self.image,
                self.label,
                structure_map_path,
                keep_mask=keep_mask,
                damage_mask=damage_mask,
                warp_mode=self.tissue_mask_warp_mode or WARP_MODE_DEFAULT,
                region_code=self.region,
                structure_map=structure_map,
                slice_id=slice_id,
            )
            warp_meta["keep_mask_source"] = self.keep_mask_source
            return warped_labels, warped_atlas, color_label, warp_meta

        warped_labels, warped_atlas, color_label = register_to_atlas(
            tissue,
            self.image,
            self.label,
            structure_map_path,
            fixed_keep_mask=None,
            moving_exclude_mask=damage_mask,
        )
        warp_meta = {
            "tissue_mask_used": False,
            "tissue_mask_warp_mode": "standard",
            "keep_mask_source": None,
            "keep_components": 0,
            "damage_mask_applied": damage_mask is not None and bool(np.any(damage_mask)),
        }
        return warped_labels, warped_atlas, color_label, warp_meta


class AlignmentController:
    """
    Handles the control flow for alignment to the atlas

    Args:
        nrrd_path (str): path to nrrd files
        is_whole (bool): deprecated; use layout_mode instead
        layout_mode (str): auto | whole | hemi — per-section or forced layout
        input_path (str): path to input images
        output_path (str): path to output alignments
        model_path (str): path to tissue predictor model
        sam_path (str): path to SAM model
        spacing (int): the spacing between sections in microns
        structures_path (str): path to structures file
    """

    def __init__(
        self,
        nrrd_path,
        input_path,
        output_path,
        structures_path,
        model_path,
        sam_path,
        spacing=None,
        is_whole=True,
        layout_mode=None,
        use_legacy=False,
        slice_filter=None,
        bundle_root=None,
    ):
        if layout_mode is None:
            layout_mode = "whole" if is_whole else "hemi"
        else:
            layout_mode = parse_layout_mode(str(layout_mode))
        self.nrrd_path = nrrd_path
        self.input_path = input_path
        self.output_path = output_path
        self.structures_path = structures_path
        if bundle_root:
            self.bundle_root = Path(bundle_root).resolve()
        else:
            resolved = resolve_bundle_root_from_dapi_dir(input_path)
            self.bundle_root = resolved
        graph_path = Path(structures_path).parent / "structure_graph.json"
        self.catalog = None
        self.parcel_ccf_advanced = False
        if graph_path.is_file():
            from structure_catalog import load_catalog

            self.catalog = load_catalog(graph_path)
        self.model_path = model_path
        self.sam_path = Path(sam_path).expanduser()
        self.spacing = spacing
        self.layout_mode = layout_mode
        self.use_legacy = use_legacy
        self.slice_filter = slice_filter
        self.viewer = napari.Viewer(
            title="Atlas Alignment",
        )

        atlas_name = "reconstructed_atlas.nrrd" if use_legacy else "atlas_10.nrrd"
        annotation_name = (
            "reconstructed_annotation.nrrd" if use_legacy else "annotation_10.nrrd"
        )

        with perf_log.perf_section("align.startup.load_nrrd"):
            self.atlas = nrrd.read(
                Path(self.nrrd_path) / atlas_name,
            )[0]
            self.annotation = nrrd.read(
                Path(self.nrrd_path) / annotation_name,
            )[0]

        # Always keep the full atlas volume; per-section hemisphere cropping happens
        # in AtlasSlice.set_slice so mixed whole/hemi series work in one session.

        # Atlas layer
        self.atlas_layer = self.viewer.add_image(
            np.zeros((1920, 1080)),
            name="Atlas",
            colormap="gray",
            contrast_limits=[0, 255],
        )

        # Tissue layer
        self.tissue_layer = self.viewer.add_image(
            np.zeros((1920, 1080)),
            name="Tissue",
            colormap="gray",
            contrast_limits=[0, 255],
        )
        # Keep Atlas and DAPI in two fixed, separate viewboxes.  The gap makes
        # the two maps read as independent panels like Viewer/Editor, while
        # avoiding a draggable splitter and its extra synchronization state.
        self.viewer.grid.shape = (1, 2)
        self.viewer.grid.stride = 1
        self.viewer.grid.spacing = 12

        self.file_list = []
        self.num_slices = 0
        self.atlas_slices = {}
        # Cache of loaded DAPI display images keyed by resolved path, so
        # revisiting a section doesn't re-read it from (possibly NAS) disk.
        # Entries are small (5% preview PNGs) and bounded by the slice count.
        self._dapi_img_cache = {}
        # Seam-corrected alignment previews are display-only and live in a
        # session temporary directory.  The alignment and final warp continue
        # to use the original DAPI input.
        self._align_seam_display_cache = {}
        self._align_seam_display_tempdir = tempfile.TemporaryDirectory(
            prefix="masonjar-align-seam-"
        )

        self.visited = 0  # The index of the furthest visited section
        self._ap_locked = False  # True when reopening a completed / fully visited session
        self.current_section = 0  # The index of the current section
        self.initial_pos = None  # The first section actually selected by the user
        self.predicted_delta = None  # The predicted delta between sections

        self.x_angle_spinbox = QDoubleSpinBox()
        self.x_angle_spinbox.setRange(-15, 15)
        self.x_angle_spinbox.setSingleStep(1.0)
        self.x_angle_spinbox.setDecimals(1)
        self.x_angle_spinbox.setSuffix("°")
        # Apply on Enter / focus-out / arrow-step, not while typing digits.
        self.x_angle_spinbox.setKeyboardTracking(False)
        self.x_angle_spinbox.valueChanged.connect(self.que_update_slice)

        self.y_angle_spinbox = QDoubleSpinBox()
        self.y_angle_spinbox.setRange(-15, 15)
        self.y_angle_spinbox.setSingleStep(1.0)
        self.y_angle_spinbox.setDecimals(1)
        self.y_angle_spinbox.setSuffix("°")
        self.y_angle_spinbox.setKeyboardTracking(False)
        self.y_angle_spinbox.valueChanged.connect(self.que_update_slice)

        self.link_angles_button = QCheckBox("Link Angles")
        self.link_angles_button.setChecked(True)
        self.link_angles_button.stateChanged.connect(self.update_linkage)

        self.ap_position_spinbox = QDoubleSpinBox()
        if not self.use_legacy:
            self.ap_position_spinbox.setRange(0, 1319)
        else:
            self.ap_position_spinbox.setRange(0, 528)
        self.ap_position_spinbox.setSingleStep(10)
        # no decimal places
        self.ap_position_spinbox.setDecimals(0)
        self.ap_position_spinbox.setKeyboardTracking(False)
        self.ap_position_spinbox.valueChanged.connect(self.que_update_position)

        # Forced AP step: when enabled, pressing Next sets the section you land on
        # to (previous section AP + step), overwriting it, instead of the auto
        # extrapolation. Only meaningful on Next (which always advances), so the
        # step is a positive spacing.
        self.forced_ap_checkbox = QCheckBox("Force AP\n(step)")
        self.forced_ap_checkbox.setToolTip(
            "When on, Next sets the next section's AP to the current AP plus the "
            "step below (overwriting it), instead of auto AP extrapolation."
        )
        self.forced_ap_spinbox = QDoubleSpinBox()
        self.forced_ap_spinbox.setRange(1, 528 if self.use_legacy else 1319)
        self.forced_ap_spinbox.setDecimals(0)
        self.forced_ap_spinbox.setSingleStep(5)
        self.forced_ap_spinbox.setValue(10)
        self.forced_ap_spinbox.setKeyboardTracking(False)

        # Debounce delay (ms) before a control change re-slices the atlas.
        # Lives in the Options dock (Performance) and is adjustable live:
        # lower = snappier, higher = fewer recomputes while holding step arrows.
        # 150ms keeps arrow-key/step-button edits responsive while still
        # coalescing a rapid sequence into one atlas re-slice.
        self._update_debounce_ms = 150
        self.debounce_spinbox = QDoubleSpinBox()
        self.debounce_spinbox.setRange(0, 2000)
        self.debounce_spinbox.setSingleStep(50)
        self.debounce_spinbox.setDecimals(0)
        self.debounce_spinbox.setSuffix(" ms")
        self.debounce_spinbox.setValue(self._update_debounce_ms)
        self.debounce_spinbox.setKeyboardTracking(False)
        self.debounce_spinbox.valueChanged.connect(self._on_debounce_changed)

        # Napari's absolute camera zoom depends on the display canvas, so 100%
        # is recorded after the initial fit.  The user adjusts this with the
        # canvas wheel; no separate sidebar control is shown.
        self._atlas_zoom_base = None
        self._atlas_zoom_syncing = False

        # Region selection
        self.region_tags = {
            "All Regions": "A",
            "Cerebrum Only": "C",
            "No Cerebrum": "NC",
        }
        self.region_selection = QComboBox()
        self.region_selection.addItems(
            [
                "All Regions",
                "Cerebrum Only",
                "No Cerebrum",
            ]
        )
        self.region_selection.currentIndexChanged.connect(self.update_region)

        self.layout_tags = {
            "Whole brain": "W",
            "Left hemisphere": "L",
        }
        self.layout_selection = QComboBox()
        self.layout_selection.addItems(list(self.layout_tags.keys()))
        self.layout_selection.currentIndexChanged.connect(self.que_update_layout)

        self.apply_layout_all_button = QPushButton("Apply layout to all")
        self.apply_layout_all_button.setToolTip(
            "Set every section's layout to the current 'Section layout' choice. "
            "Use for samples that are entirely one hemisphere."
        )
        self.apply_layout_all_button.setFixedHeight(
            self.layout_selection.sizeHint().height()
        )
        self.apply_layout_all_button.clicked.connect(self.apply_layout_to_all)

        self.parcel_selection = QComboBox()
        self.parcel_advanced = QCheckBox("Advanced CCFv3")
        self.parcel_level_combo = QComboBox()
        self.parcel_level_combo.setEnabled(False)
        self.parcellation_finish_note = QLabel("Parcellation (all sections on Finish)")
        self.parcellation_finish_note.setWordWrap(True)
        self._init_parcellation_controls()

        self.tissue_mask_checkbox = QCheckBox("Use tissue-cleanup mask")
        self.tissue_mask_checkbox.setToolTip(
            "Use the tissue edge-cleanup mask for atlas warping."
        )
        # New alignment sessions start with the original DAPI warp path.
        # A saved session keeps its per-section choice when it is restored.
        self.tissue_mask_checkbox.setChecked(False)
        self.tissue_mask_checkbox.stateChanged.connect(self._on_tissue_mask_toggled)

        self.seam_display_toggle = QPushButton("Seam correction")
        self.seam_display_toggle.setCheckable(True)
        self.seam_display_toggle.setToolTip(
            "Show a live seam-corrected DAPI preview. Alignment warp and "
            "prediction still use the original DAPI image."
        )
        self.seam_display_toggle.toggled.connect(
            lambda _s: self.update_display()
        )

        self.tissue_mask_mode_combo = QComboBox()
        for mode_id, label in WARP_MODE_CHOICES:
            self.tissue_mask_mode_combo.addItem(label, mode_id)
        self.tissue_mask_mode_combo.currentIndexChanged.connect(
            self._on_tissue_mask_mode_changed
        )
        self.tissue_mask_mode_combo.setVisible(False)

        self.tissue_mask_status = QLabel("")
        self.tissue_mask_status.setWordWrap(True)
        self.tissue_mask_status.setAlignment(QtCore.Qt.AlignmentFlag.AlignLeading)

        # Both right docks share one column.  Keep their controls usable in a
        # compact dock rather than letting Qt's default size hints lock the
        # splitter at a wide width.
        for control in (
            self.x_angle_spinbox,
            self.y_angle_spinbox,
            self.ap_position_spinbox,
            self.region_selection,
            self.forced_ap_spinbox,
            self.layout_selection,
            self.parcel_selection,
            self.parcel_level_combo,
            self.tissue_mask_mode_combo,
            self.debounce_spinbox,
        ):
            control.setMinimumWidth(70)
            control.setSizePolicy(
                QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred
            )

        self.section_info_label = QLabel("")
        self.section_info_label.setWordWrap(True)
        self.section_info_label.setAlignment(
            QtCore.Qt.AlignmentFlag.AlignLeft | QtCore.Qt.AlignmentFlag.AlignTop
        )
        self.section_info_label.setMinimumWidth(180)

        self.flag_section_button = QPushButton("Flag section…")
        self.flag_section_button.clicked.connect(self.flag_current_section)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(1, self.num_slices)
        self.progress_bar.setValue(1)
        self.progress_bar.setFixedWidth(800)
        self.progress_bar.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)

        self.next_button = QPushButton("Next")
        self.next_button.clicked.connect(self.next_section)

        self.previous_button = QPushButton("Previous")
        self.previous_button.clicked.connect(self.previous_section)

        self.finish_button = QPushButton("Finish")
        self.finish_button.clicked.connect(self.finish)

        self.goto_button = QPushButton("Go to…")
        self.goto_button.clicked.connect(self.prompt_goto_section)

        # Timers
        self.slice_update_timer = QTimer()
        self.slice_update_timer.setSingleShot(True)
        self.slice_update_timer.timeout.connect(self.update_slice)
        self.pos_update_timer = QTimer()
        self.pos_update_timer.setSingleShot(True)
        self.pos_update_timer.timeout.connect(self.update_position)
        self._autosave_timer = QTimer()
        self._autosave_timer.setSingleShot(True)
        self._autosave_timer.timeout.connect(
            lambda: self.persist_alignment_session("debounced")
        )
        self._session_restore_nav = False
        self._session_finished = False
        self._viewer_close_handshake_sent = False
        self._controls_seeded = False
        # Background saver: disk I/O for autosave runs off the Qt main thread
        # so slice navigation is not blocked by NAS writes. A single worker
        # writes the most-recent payload only (coalescing). Terminal saves
        # (close/finish) flush synchronously to guarantee the final state.
        self._save_lock = threading.Lock()
        self._save_pending = None  # latest payload awaiting write
        self._save_wake = threading.Event()
        self._save_idle = threading.Event()
        self._save_idle.set()
        self._save_stop = False
        self._save_thread = threading.Thread(
            target=self._background_saver_loop, daemon=True
        )
        self._save_thread.start()
        self._save_exit_timer = QTimer()
        self._save_exit_flag = Path(self.input_path) / ".align_save_exit"
        self._align_toolbar = None
        self._tuning_dock = None
        self._options_dock = None
        self._options_settings = QtCore.QSettings("MasonJar", "MasonJar")

        # One scroll area owns the whole right sidebar.  Separate Tuning and
        # Options docks made only the lower panel reliably scrollable when the
        # window was short, while the upper panel could become inaccessible.
        self.sidebar_sections = ReorderableSidebarSections()
        self.sidebar_sections.add_section(
            "tuning",
            "Tuning",
            [
                self._labeled_control_row("X angle:", self.x_angle_spinbox),
                self._labeled_control_row("Y angle:", self.y_angle_spinbox),
                self._labeled_control_row("AP position:", self.ap_position_spinbox),
                self.link_angles_button,
                self._labeled_control_row("Regions:", self.region_selection),
                self._inline_control_row(
                    self.forced_ap_checkbox,
                    self.forced_ap_spinbox,
                ),
            ],
        )
        self.sidebar_sections.add_section(
            "layout",
            "Layout",
            [
                self._labeled_control_row("Layout:", self.layout_selection),
                self._labeled_control_row(
                    "Layout:",
                    self.apply_layout_all_button,
                    placeholder_label=True,
                ),
            ],
        )
        self.sidebar_sections.add_section(
            "parcellation",
            "Parcellation",
            [
                self.parcellation_finish_note,
                self._labeled_control_row("Roll up to:", self.parcel_selection),
                self.parcel_advanced,
                self._labeled_control_row("Level:", self.parcel_level_combo),
            ],
        )
        self.sidebar_sections.add_section(
            "warp_options",
            "Warp options",
            [
                self.tissue_mask_checkbox,
                QLabel("Gap warp strategy"),
                self.tissue_mask_mode_combo,
                self.tissue_mask_status,
            ],
        )
        self.sidebar_sections.add_section(
            "performance",
            "Performance",
            [
                QLabel("Update delay"),
                self.debounce_spinbox,
            ],
        )
        self._restore_sidebar_section_order()
        self.sidebar_sections.orderChanged.connect(
            self._save_sidebar_section_order
        )
        self.sidebar_sections.collapsedChanged.connect(
            self._save_sidebar_collapsed_sections
        )
        sidebar_panel = build_scroll_dock_panel(
            [self.sidebar_sections],
            # The sidebar previously ended 4px after its controls.  Reserve a
            # further 5px on the right so fields do not touch the dock edge.
            margins=(4, 4, 9, 4),
            # Separate buttons and fields from the vertical scrollbar itself.
            content_margins=(0, 0, 5, 0),
        )
        self._options_dock = self.viewer.window.add_dock_widget(
            sidebar_panel,
            area="right",
            name="Options",
            add_vertical_stretch=False,
        )
        # Napari's dock content size hints otherwise prevent the user from
        # dragging the shared right boundary inward.  180px still leaves a
        # usable label/control row (80px label + 70px field + spacing).
        sidebar_panel.setMinimumWidth(0)
        sidebar_panel.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Expanding
        )
        self._options_dock.setMinimumWidth(180)
        stored_width = self._options_settings.value(
            "alignment/optionsDockWidth", 0
        )
        try:
            self._saved_options_dock_width = max(0, int(stored_width))
        except (TypeError, ValueError):
            self._saved_options_dock_width = 0
        self._options_width_ready = False
        self._options_width_filter = AtlasSidebarWidthFilter(
            self, self._options_dock
        )
        self._options_dock.installEventFilter(self._options_width_filter)

        self._relayout_pending = False
        self._bind_dock_relayout_signals()

        qt_window = resolve_napari_qt_window(self.viewer)
        if qt_window is not None:
            self._qt_window = qt_window
            self._init_align_toolbar(qt_window)
            self._init_panels_menu(qt_window)
            self._shortcut_keys = align_shortcuts.load_shortcuts(
                _ALIGN_SHORTCUT_DEFAULTS
            )
            self._qshortcuts = {}
            self._apply_shortcuts()
            self._init_help_menu(qt_window)

        with perf_log.perf_section("align.startup.scan_input"):
            self.scan_input()

        self.prior_alignment = False
        with perf_log.perf_section("align.startup.load_alignment"):
            self.load_alignment()

        if not self.prior_alignment:
            with perf_log.perf_section("align.startup.predict"):
                self.predict_sample_slices()
        else:
            self.region_selection.setCurrentIndex(
                list(self.region_tags.values()).index(
                    self.atlas_slices[self.file_list[self.current_section]].region
                )
            )
            self._sync_layout_selection_from_slice()
            if self._session_restore_nav:
                self.progress_bar.setValue(self.current_section + 1)
                self.progress_bar.setFormat(
                    f"{self.current_section + 1} / {self.num_slices}"
                )

        print("Awaiting fine tuning...", flush=True)

        with perf_log.perf_section("align.startup.start_viewer"):
            self.start_viewer()

    @staticmethod
    def _labeled_control_row(label_text, control, *, placeholder_label=False):
        """One compact label/control row for Alignment dock panels."""
        row = QWidget()
        layout = QHBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        label = QLabel(label_text)
        label.setMinimumWidth(80)
        label.setSizePolicy(
            QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Preferred
        )
        label.setAlignment(
            QtCore.Qt.AlignmentFlag.AlignLeft | QtCore.Qt.AlignmentFlag.AlignVCenter
        )
        if placeholder_label:
            # Reserve the exact label geometry from the row above without
            # repeating its text.  An empty label only retained the 80px
            # minimum, whereas the visible row's label can have a wider size
            # hint.
            label.setStyleSheet("color: transparent;")
        layout.addWidget(label)
        control.setMinimumWidth(max(70, control.minimumWidth()))
        control.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred
        )
        layout.addWidget(control, 1)
        row.setLayout(layout)
        return row

    @staticmethod
    def _inline_control_row(leading_control, trailing_control):
        """Align a checkbox and its dependent field with labelled controls."""
        row = QWidget()
        layout = QHBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        # Match _labeled_control_row's 80px label column so the dependent
        # input begins on the same vertical line as X/Y/AP/Regions fields.
        leading_control.setFixedWidth(80)
        leading_control.setSizePolicy(
            QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Preferred
        )
        trailing_control.setMinimumWidth(max(70, trailing_control.minimumWidth()))
        trailing_control.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred
        )
        layout.addWidget(leading_control)
        layout.addWidget(trailing_control, 1)
        row.setLayout(layout)
        return row

    def _restore_sidebar_section_order(self):
        """Restore a valid user-defined sidebar order, if one was saved."""
        raw = self._options_settings.value("alignment/sidebarSectionOrder", "")
        try:
            order = json.loads(str(raw)) if raw else []
        except (TypeError, ValueError, json.JSONDecodeError):
            order = []
        if isinstance(order, list):
            self.sidebar_sections.set_order(order)
        raw_collapsed = self._options_settings.value(
            "alignment/sidebarCollapsedSections", ""
        )
        try:
            collapsed = json.loads(str(raw_collapsed)) if raw_collapsed else []
        except (TypeError, ValueError, json.JSONDecodeError):
            collapsed = []
        if isinstance(collapsed, list):
            self.sidebar_sections.set_collapsed_sections(collapsed)

    def _save_sidebar_section_order(self, order):
        """Persist the sidebar order after an internal heading drag."""
        self._options_settings.setValue(
            "alignment/sidebarSectionOrder", json.dumps(list(order))
        )

    def _save_sidebar_collapsed_sections(self, collapsed):
        """Persist the user's per-section collapse choices."""
        self._options_settings.setValue(
            "alignment/sidebarCollapsedSections", json.dumps(list(collapsed))
        )

    def _init_align_toolbar(self, qt_window):
        """Top toolbar: section info, nav, and flag."""
        self._align_toolbar = QToolBar("Alignment", qt_window)
        self._align_toolbar.setObjectName("MasonJarAlignmentToolbar")
        self._align_toolbar.setMovable(False)
        qt_window.addToolBar(QtCore.Qt.ToolBarArea.TopToolBarArea, self._align_toolbar)

        self._align_toolbar.addWidget(self.section_info_label)
        self._align_toolbar.addSeparator()
        leading_spacer = QWidget(self._align_toolbar)
        leading_spacer.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred
        )
        self._align_toolbar.addWidget(leading_spacer)
        self._align_toolbar.addWidget(self.progress_bar)
        trailing_spacer = QWidget(self._align_toolbar)
        trailing_spacer.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred
        )
        self._align_toolbar.addWidget(trailing_spacer)
        self._align_toolbar.addSeparator()
        self.options_dock_button = QPushButton("Options", self._align_toolbar)
        self.options_dock_button.setCheckable(True)
        # The enclosing Napari window is not shown yet, so isVisible() would
        # be false even though the newly created dock is enabled by default.
        self.options_dock_button.setChecked(True)
        self.options_dock_button.setToolTip(
            "Show or hide the complete Tuning and Options sidebar."
        )
        self.options_dock_button.toggled.connect(self._toggle_options_dock)
        if self._options_dock is not None:
            self._options_dock.visibilityChanged.connect(
                self._on_options_dock_visibility
            )
        navigation_grid = QWidget(self._align_toolbar)
        navigation_layout = QGridLayout(navigation_grid)
        navigation_layout.setContentsMargins(0, 0, 0, 0)
        navigation_layout.setSpacing(2)
        navigation_layout.setColumnStretch(0, 1)
        navigation_layout.setColumnStretch(1, 1)
        navigation_layout.setRowStretch(0, 1)
        navigation_layout.setRowStretch(1, 1)
        for button in (
            self.goto_button,
            self.finish_button,
            self.previous_button,
            self.next_button,
        ):
            # Keep the navigation grid visually stable regardless of each
            # translated button label.  The 2px grid gap remains between the
            # two 120px columns.
            button.setFixedWidth(120)
            button.setSizePolicy(
                QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding
            )
        navigation_layout.addWidget(self.goto_button, 0, 0)
        navigation_layout.addWidget(self.finish_button, 0, 1)
        navigation_layout.addWidget(self.previous_button, 1, 0)
        navigation_layout.addWidget(self.next_button, 1, 1)
        self._align_toolbar.addWidget(navigation_grid)
        self._align_toolbar.addSeparator()
        right_actions = QWidget(self._align_toolbar)
        right_actions_layout = QVBoxLayout(right_actions)
        right_actions_layout.setContentsMargins(0, 0, 0, 0)
        right_actions_layout.setSpacing(2)
        for button in (
            self.options_dock_button,
            self.seam_display_toggle,
            self.flag_section_button,
        ):
            button.setSizePolicy(
                QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
            )
            right_actions_layout.addWidget(button)
        self._align_toolbar.addWidget(right_actions)
        header_right_margin = QWidget(self._align_toolbar)
        header_right_margin.setFixedWidth(20)
        self._align_toolbar.addWidget(header_right_margin)

    def _toggle_options_dock(self, visible: bool):
        """Show or hide the complete right sidebar."""
        if self._options_dock is not None:
            self._options_dock.setVisible(bool(visible))

    def _on_options_dock_visibility(self, visible: bool):
        """Keep the toolbar toggle correct after a dock title-bar close/show."""
        button = getattr(self, "options_dock_button", None)
        if button is None:
            return
        button.blockSignals(True)
        button.setChecked(bool(visible))
        button.blockSignals(False)

    def _apply_default_options_width(self):
        """Restore saved sidebar width, or reserve one quarter of the screen."""
        dock = self._options_dock
        qt_window = getattr(self, "_qt_window", None)
        if dock is None or qt_window is None or not dock.isVisible():
            return
        screen = qt_window.screen() or QApplication.primaryScreen()
        if screen is None:
            return
        target_width = self._saved_options_dock_width or round(
            screen.availableGeometry().width() * 0.25
        )
        minimum_width = max(dock.minimumWidth(), dock.minimumSizeHint().width())
        self._options_width_ready = True
        qt_window.resizeDocks(
            [dock],
            [max(target_width, minimum_width)],
            QtCore.Qt.Orientation.Horizontal,
        )

    def _schedule_default_options_width(self):
        """Apply width after Qt completes its maximize-and-dock layout pass."""
        QTimer.singleShot(0, self._apply_default_options_width)
        QTimer.singleShot(200, self._apply_default_options_width)

    def _initialize_atlas_zoom_policy(self):
        """Use the fitted Napari camera zoom as Viewer-like 100%."""
        if self._atlas_zoom_base is not None:
            return
        try:
            zoom = float(self.viewer.camera.zoom)
            if zoom <= 0:
                return
            self._atlas_zoom_base = zoom
            self.viewer.camera.events.zoom.connect(
                self._on_atlas_camera_zoom_changed
            )
            self._sync_atlas_zoom_controls(100)
        except Exception:
            # Zoom controls must never prevent the alignment viewer opening.
            return

    def _sync_atlas_zoom_controls(self, percent: int):
        """Reserved hook for zoom-status consumers; no sidebar control exists."""
        return

    def _on_atlas_camera_zoom_changed(self, event):
        """Clamp mouse zoom to the same 50–1000% Viewer/Editor range."""
        if self._atlas_zoom_syncing or self._atlas_zoom_base is None:
            return
        try:
            actual = float(event.value)
            percent = round(actual / self._atlas_zoom_base * 100)
            clamped = max(50, min(1000, percent))
            if clamped != percent:
                self._atlas_zoom_syncing = True
                try:
                    self.viewer.camera.zoom = self._atlas_zoom_base * (
                        clamped / 100.0
                    )
                finally:
                    self._atlas_zoom_syncing = False
            self._sync_atlas_zoom_controls(clamped)
        except Exception:
            return

    def _init_panels_menu(self, qt_window):
        """Add panel show/hide toggles under napari's existing 'Window' menu."""
        try:
            menubar = qt_window.menuBar()
        except Exception:
            menubar = None
        if menubar is None:
            return
        menu = None
        try:
            for action in menubar.actions():
                sub = action.menu()
                if sub is None:
                    continue
                if sub.title().replace("&", "").strip().lower() == "window":
                    menu = sub
                    break
        except Exception:
            menu = None
        if menu is None:
            # Fallback if napari exposes no Window menu.
            menu = menubar.addMenu("Window")
        menu.addSeparator()
        self._panel_targets = []
        for widget, label in (
            (self._align_toolbar, "Toolbar"),
            (self._options_dock, "Tuning and Options sidebar"),
        ):
            if widget is None:
                continue
            try:
                act = widget.toggleViewAction()
                act.setText(label)
                menu.addAction(act)
                self._panel_targets.append(widget)
            except Exception:
                pass
        menu.addSeparator()
        menu.addAction("Show all panels", lambda: self._set_all_panels(True))
        menu.addAction("Hide all panels", lambda: self._set_all_panels(False))
        menu.addAction("Reset panel layout", lambda: self._reset_panels_layout())

    def _set_all_panels(self, visible):
        for widget in getattr(self, "_panel_targets", []):
            try:
                widget.setVisible(visible)
            except Exception:
                pass

    def _apply_shortcuts(self):
        """(Re)bind QShortcuts for alignment actions from self._shortcut_keys."""
        from qtpy.QtGui import QKeySequence
        from qtpy.QtWidgets import QShortcut

        qt_window = getattr(self, "_qt_window", None)
        if qt_window is None:
            return
        for sc in getattr(self, "_qshortcuts", {}).values():
            try:
                sc.setParent(None)
                sc.deleteLater()
            except Exception:
                pass
        self._qshortcuts = {}
        keys = getattr(self, "_shortcut_keys", {}) or {}
        for aid, _label in _ALIGN_SHORTCUT_ACTIONS:
            key = keys.get(aid, "")
            if not key:
                continue
            handler = getattr(self, aid, None)
            if not callable(handler):
                continue
            try:
                sc = QShortcut(QKeySequence(key), qt_window)
                sc.activated.connect(handler)
                self._qshortcuts[aid] = sc
            except Exception:
                pass
        self._update_shortcut_tooltips()

    def _update_shortcut_tooltips(self):
        """Append each action's current shortcut to its button's hover tooltip.

        The original tooltip (captured once) is preserved so re-applying after an
        edit never duplicates or loses the base text.
        """
        keys = getattr(self, "_shortcut_keys", {}) or {}
        labels = dict(_ALIGN_SHORTCUT_ACTIONS)
        if not hasattr(self, "_base_tooltips"):
            self._base_tooltips = {}
        for aid, btn_attr in _ALIGN_SHORTCUT_BUTTONS.items():
            btn = getattr(self, btn_attr, None)
            if btn is None:
                continue
            if btn_attr not in self._base_tooltips:
                try:
                    self._base_tooltips[btn_attr] = btn.toolTip() or ""
                except Exception:
                    self._base_tooltips[btn_attr] = ""
            base = self._base_tooltips[btn_attr] or labels.get(aid, aid)
            key = keys.get(aid, "")
            try:
                btn.setToolTip(f"{base}  (Shortcut: {key})" if key else base)
            except Exception:
                pass

    def _init_help_menu(self, qt_window):
        """Add a 'Shortcuts…' item under napari's existing Help menu."""
        try:
            menubar = qt_window.menuBar()
        except Exception:
            menubar = None
        if menubar is None:
            return
        menu = None
        try:
            for action in menubar.actions():
                sub = action.menu()
                if sub is None:
                    continue
                if sub.title().replace("&", "").strip().lower() == "help":
                    menu = sub
                    break
        except Exception:
            menu = None
        if menu is None:
            menu = menubar.addMenu("Help")
        menu.addSeparator()
        menu.addAction("Shortcuts…", self._open_shortcuts_dialog)

    def _open_shortcuts_dialog(self):
        try:
            dlg = align_shortcuts.ShortcutsDialog(
                getattr(self, "_qt_window", None),
                _ALIGN_SHORTCUT_ACTIONS,
                getattr(self, "_shortcut_keys", {}) or {},
                _ALIGN_SHORTCUT_DEFAULTS,
                self._on_shortcuts_applied,
            )
            dlg.exec()
        except Exception as exc:
            print(f"shortcuts dialog error: {exc}", flush=True)

    def _on_shortcuts_applied(self, mapping):
        self._shortcut_keys = dict(mapping)
        self._apply_shortcuts()

    def _reset_panels_layout(self):
        """Restore the combined sidebar to the right dock area."""
        widget = self._options_dock
        if widget is None:
            return
        try:
            if hasattr(widget, "setFloating"):
                widget.setFloating(False)
            widget.setVisible(True)
        except Exception:
            pass
        arrange_right_docks_split(self.viewer, None, widget)

    def _bind_dock_relayout_signals(self):
        """Restore the sidebar to its dock area after it is re-docked."""
        dock = self._options_dock
        if dock is None:
            return
        try:
            dock.topLevelChanged.connect(self._on_dock_top_level_changed)
        except Exception:
            pass

    def _on_dock_top_level_changed(self, floating):
        # Only act when a dock is re-docked (floating -> False).
        if floating or getattr(self, "_relayout_pending", False):
            return
        self._relayout_pending = True
        try:
            from qtpy.QtCore import QTimer

            # Defer so Qt finishes the (un)dock operation before restoring it.
            QTimer.singleShot(0, self._apply_dock_relayout)
        except Exception:
            self._apply_dock_relayout()

    def _apply_dock_relayout(self):
        self._relayout_pending = False
        arrange_right_docks_split(self.viewer, None, self._options_dock)

    def _show_align_chrome(self):
        """Force Mason Jar docks visible; hide unused napari layer docks.

        The combined Tuning/Options sidebar lives on the right where the layer
        list and layer controls used to be.
        """
        hide_napari_layer_docks(self.viewer)
        ensure_qt_dock_visible(self._align_toolbar)
        ensure_qt_dock_visible(self._options_dock)
        arrange_right_docks_split(self.viewer, None, self._options_dock)

    @staticmethod
    def _slice_id_from_filename(name: str) -> str:
        stem = ".".join(name.split(".")[:-1]) if "." in name else name
        return stem.split(".")[0]

    def _init_parcellation_controls(self):
        from structure_catalog import (
            CCF_ADVANCED_HELP,
            FULL_DETAIL_TIER,
            compact_ccf_level_label_and_tooltip,
            list_ccf_levels,
            list_tiers,
        )

        self.parcel_selection.clear()
        self.parcel_selection.addItem("Full detail", FULL_DETAIL_TIER)
        self.parcel_selection.setItemData(
            0,
            "Keep annotation IDs as drawn (no rollup).",
            QtCore.Qt.ItemDataRole.ToolTipRole,
        )
        if self.catalog:
            for tier in list_tiers(self.catalog):
                self.parcel_selection.addItem(tier["label"], tier["id"])
                tip = tier.get("description") or ""
                if tier["id"] == "layers":
                    tip = tip or "Laminar resolution for paint / rollup."
                self.parcel_selection.setItemData(
                    self.parcel_selection.count() - 1,
                    tip,
                    QtCore.Qt.ItemDataRole.ToolTipRole,
                )
            self.parcel_level_combo.clear()
            for info in list_ccf_levels(self.catalog):
                label, tooltip = compact_ccf_level_label_and_tooltip(info)
                self.parcel_level_combo.addItem(label, info["level"])
                self.parcel_level_combo.setItemData(
                    self.parcel_level_combo.count() - 1,
                    tooltip,
                    QtCore.Qt.ItemDataRole.ToolTipRole,
                )
            self._show_current_combo_item_tooltip(self.parcel_level_combo)
        else:
            self.parcel_selection.setEnabled(False)
            self.parcel_advanced.setEnabled(False)
            self.parcel_level_combo.setEnabled(False)
            return

        self.parcel_advanced.toggled.connect(self._on_parcel_advanced_toggled)
        self.parcel_advanced.setToolTip(CCF_ADVANCED_HELP)
        self.parcel_selection.currentIndexChanged.connect(
            lambda _idx: self.schedule_autosave("parcellation")
        )
        self.parcel_level_combo.currentIndexChanged.connect(
            self._on_parcel_level_changed
        )

    def _on_parcel_advanced_toggled(self, checked: bool):
        self.parcel_ccf_advanced = bool(checked)
        # Preserve both controls for a stable, accessible layout. Only the
        # relevant target is editable in the selected mode.
        self.parcel_selection.setEnabled(not self.parcel_ccf_advanced)
        self.parcel_level_combo.setEnabled(self.parcel_ccf_advanced)
        self.schedule_autosave("parcellation")

    @staticmethod
    def _show_current_combo_item_tooltip(combo: QComboBox) -> None:
        idx = combo.currentIndex()
        tooltip = combo.itemData(idx, QtCore.Qt.ItemDataRole.ToolTipRole)
        combo.setToolTip(str(tooltip or ""))

    def _on_parcel_level_changed(self, _index: int):
        self._show_current_combo_item_tooltip(self.parcel_level_combo)
        self.schedule_autosave("parcellation")

    def _tuning_fingerprint(self) -> str:
        return compute_tuning_fingerprint(
            self.file_list,
            self.layout_mode,
            self.use_legacy,
            self.slice_filter,
        )

    def _parcellation_state(self) -> dict:
        from structure_catalog import FULL_DETAIL_TIER

        if self.parcel_ccf_advanced:
            level = self.parcel_level_combo.currentData()
            return {
                "ccf_advanced": True,
                "st_level": int(level) if level is not None else None,
                "tier_id": None,
            }
        tier = self.parcel_selection.currentData()
        return {
            "ccf_advanced": False,
            "st_level": None,
            "tier_id": str(tier) if tier is not None else FULL_DETAIL_TIER,
        }

    def _apply_parcellation_state(self, state: dict | None) -> None:
        if not state or not self.catalog:
            return
        self.parcel_selection.blockSignals(True)
        self.parcel_level_combo.blockSignals(True)
        self.parcel_advanced.blockSignals(True)
        try:
            if state.get("ccf_advanced"):
                self.parcel_advanced.setChecked(True)
                level = state.get("st_level")
                if level is not None:
                    idx = self.parcel_level_combo.findData(level)
                    if idx >= 0:
                        self.parcel_level_combo.setCurrentIndex(idx)
            else:
                self.parcel_advanced.setChecked(False)
                tier = state.get("tier_id")
                if tier is not None:
                    idx = self.parcel_selection.findData(tier)
                    if idx >= 0:
                        self.parcel_selection.setCurrentIndex(idx)
            self.parcel_ccf_advanced = self.parcel_advanced.isChecked()
            self.parcel_selection.setEnabled(not self.parcel_ccf_advanced)
            self.parcel_level_combo.setEnabled(self.parcel_ccf_advanced)
            self._show_current_combo_item_tooltip(self.parcel_level_combo)
        finally:
            self.parcel_selection.blockSignals(False)
            self.parcel_level_combo.blockSignals(False)
            self.parcel_advanced.blockSignals(False)

    def _bind_slice_autosave(self, atlas_slice: AtlasSlice) -> None:
        atlas_slice._autosave_cb = lambda: self.persist_alignment_session("damage_mask")

    def schedule_autosave(self, reason: str) -> None:
        immediate = {
            "next_section",
            "previous_section",
            "damage_mask",
            "finish",
            "predict_complete",
            "edit",
            "viewer_close",
            "window_close",
            "cancel",
        }
        if reason in immediate:
            self._flush_autosave(reason)
            return
        self._autosave_timer.stop()
        self._autosave_timer.start(300)

    def _sync_current_slice_from_controls(self) -> None:
        """Commit pending spinbox edits and current control values before save/nav."""
        if not self._controls_seeded:
            return
        if self.slice_update_timer.isActive():
            self.slice_update_timer.stop()
        if self.pos_update_timer.isActive():
            self.pos_update_timer.stop()
        self._apply_current_controls_to_slice()

    def _apply_current_controls_to_slice(self) -> None:
        current_slice = self._current_atlas_slice()
        if current_slice is None:
            return
        layout_text = self.layout_selection.currentText()
        hemisphere = (
            self.layout_tags[layout_text]
            if layout_text in self.layout_tags
            else getattr(current_slice, "hemisphere", "W")
        )
        mode_id = self.tissue_mask_mode_combo.currentData()
        apply_slice_tuning_from_controls(
            current_slice,
            x_angle=self.x_angle_spinbox.value(),
            y_angle=self.y_angle_spinbox.value(),
            ap_position=self.ap_position_spinbox.value(),
            region=self.region_tags[self.region_selection.currentText()],
            hemisphere=hemisphere,
            linked=self.link_angles_button.isChecked(),
            use_tissue_cleanup_mask=self.tissue_mask_checkbox.isChecked(),
            tissue_mask_warp_mode=str(mode_id) if mode_id else "",
        )
        if current_slice.linked:
            for sl in self.atlas_slices.values():
                if sl.linked:
                    sl.x_angle = current_slice.x_angle
                    sl.y_angle = current_slice.y_angle
        current_slice.set_slice(self.atlas, self.annotation)

    def _flush_autosave(self, reason: str) -> None:
        self._autosave_timer.stop()
        if should_sync_controls_before_autosave(reason, self._controls_seeded):
            self._sync_current_slice_from_controls()
        self.persist_alignment_session(reason)

    # Reasons that must be on disk before the app can exit/hand off. These
    # submit as usual but then block until the background writer has drained.
    _TERMINAL_SAVE_REASONS = frozenset(
        {"finish", "viewer_close", "window_close", "cancel"}
    )

    def persist_alignment_session(self, reason: str) -> None:
        if not self.atlas_slices:
            return
        # Build the picklable snapshot on the main thread (cheap after the
        # image/label detach optimization); hand the disk I/O to the worker.
        try:
            payload = build_session_payload(
                self.input_path,
                self.atlas_slices,
                tuning_fingerprint=self._tuning_fingerprint(),
                output_path=self.output_path,
                current_section=self.current_section,
                visited=self.visited,
                parcellation=self._parcellation_state(),
                reason=reason,
                layout_mode=self.layout_mode,
            )
        except Exception as exc:
            print(
                f"LOG: align_session_snapshot_failed reason={reason} error={exc}",
                flush=True,
            )
            return
        self._submit_background_save(payload, reason, len(self.atlas_slices))
        if reason in self._TERMINAL_SAVE_REASONS:
            self._flush_saves_blocking()

    def _submit_background_save(self, payload, reason, n_files) -> None:
        """Queue the latest payload for the background writer (coalescing)."""
        with self._save_lock:
            self._save_pending = (payload, reason, n_files)
            self._save_idle.clear()
            self._save_wake.set()

    def _background_saver_loop(self) -> None:
        while True:
            self._save_wake.wait()
            self._save_wake.clear()
            if self._save_stop and self._save_pending is None:
                self._save_idle.set()
                return
            while True:
                with self._save_lock:
                    item = self._save_pending
                    self._save_pending = None
                if item is None:
                    break
                payload, reason, n_files = item
                try:
                    write_session_payload(payload)
                    print(
                        f"LOG: align_session_saved reason={reason} files={n_files}",
                        flush=True,
                    )
                except Exception as exc:
                    print(
                        f"LOG: align_session_save_failed reason={reason} error={exc}",
                        flush=True,
                    )
            with self._save_lock:
                if self._save_pending is None:
                    self._save_idle.set()

    def _flush_saves_blocking(self, timeout: float = 30.0) -> None:
        """Block until the background writer has written all queued payloads."""
        self._save_wake.set()
        self._save_idle.wait(timeout)

    def save_alignment(self):
        """Save alignment tuning and session sidecar (atomic pickle + JSON)."""
        self.persist_alignment_session("finish")

    def _parcel_target(self) -> tuple[str | None, int | None]:
        from structure_catalog import FULL_DETAIL_TIER

        if not self.catalog:
            return FULL_DETAIL_TIER, None
        if self.parcel_ccf_advanced:
            level = self.parcel_level_combo.currentData()
            return None, int(level) if level is not None else None
        tier_id = self.parcel_selection.currentData()
        if tier_id == FULL_DETAIL_TIER:
            return FULL_DETAIL_TIER, None
        return str(tier_id) if tier_id else None, None

    def scan_input(self):
        """Scan the input path for valid images and add to file_list"""
        img_ext = [".png", ".jpg", ".jpeg"]
        self.file_list = [
            name
            for name in os.listdir(self.input_path)
            if os.path.isfile(Path(self.input_path) / name)
            and not name.startswith(".")
            and name.endswith(tuple(img_ext))
        ]
        # Natural sort so non-zero-padded names (s1, s2, …, s10, s11) order
        # correctly instead of lexicographically (s1, s10, s11, s2, …), which
        # would otherwise scramble the section order and AP extrapolation.
        self.file_list = natural_sort_filenames(self.file_list)
        if self.slice_filter is not None:
            self.file_list = [
                name
                for name in self.file_list
                if self._slice_id_from_filename(name) in self.slice_filter
            ]
        self.num_slices = len(self.file_list)
        print(4 + self.num_slices, flush=True)
        print("Scanned input path for images...", flush=True)
        if self.num_slices == 0:
            print("No images found!", flush=True)
            exit(1)
        self.progress_bar.setRange(1, self.num_slices)
        self.progress_bar.setValue(1)
        self.progress_bar.setFormat(f"1 / {self.num_slices}")
        self.update_section_header()

    def _alignment_flags_path(self) -> Path:
        """Per AGENTS.md: flags next to alignment outputs under ``<slices>/.masonjar/``."""
        return Path(self.output_path) / ".masonjar" / "alignment_flags.json"

    def update_section_header(self):
        """Slice index + filename in the dock; sync Napari / OS window title."""
        if not self.file_list or self.num_slices == 0:
            self.section_info_label.setText("")
            return
        idx = self.current_section
        fname = self.file_list[idx]
        n = idx + 1
        m = self.num_slices
        line1 = f"Slice {n:02d} of {m:02d}"
        current = self.atlas_slices.get(fname)
        layout_line = ""
        if current is not None:
            src = "override" if current.layout_overridden else "auto"
            layout_line = (
                f"Layout: {current.layout_label()}\n({src}, "
                f"conf {current.layout_confidence:.2f})"
            )
            if current.layout_low_confidence and not current.layout_overridden:
                layout_line += "\nReview layout (low confidence)"
        text = f"{line1}\n{fname}"
        if layout_line:
            text += f"\n{layout_line}"
        self.section_info_label.setText(text)
        title = f"Atlas Alignment — {line1} — {fname}"
        self.viewer.title = title
        try:
            win = getattr(self.viewer.window, "_qt_window", None)
            if win is not None:
                win.setWindowTitle(title)
        except Exception:
            pass

    def flag_current_section(self):
        """Append one JSON object per line to ``<output>/.masonjar/alignment_flags.json``."""
        if not self.file_list or self.num_slices == 0:
            return
        fname = self.file_list[self.current_section]
        slice_id = self._slice_id_from_filename(fname)
        note, ok = QInputDialog.getText(
            None,
            "Flag section",
            "Note (saved next to alignment outputs):",
        )
        if not ok:
            return
        record = {
            "sliceId": slice_id,
            "filename": fname,
            "index": int(self.current_section),
            "note": note or "",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        out_path = self._alignment_flags_path()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        print(f"Recorded flag for {slice_id} in {out_path}", flush=True)

    def _current_atlas_slice(self) -> AtlasSlice | None:
        if not self.file_list or self.num_slices == 0:
            return None
        return self.atlas_slices[self.file_list[self.current_section]]

    def _on_tissue_mask_toggled(self, _state):
        current = self._current_atlas_slice()
        if current is None:
            return
        current.use_tissue_cleanup_mask = self.tissue_mask_checkbox.isChecked()
        self.tissue_mask_mode_combo.setVisible(current.use_tissue_cleanup_mask)
        self._sync_tissue_mask_status()
        self.schedule_autosave("tissue_mask")

    def _on_tissue_mask_mode_changed(self, _index):
        current = self._current_atlas_slice()
        if current is None:
            return
        mode_id = self.tissue_mask_mode_combo.currentData()
        if mode_id:
            current.tissue_mask_warp_mode = str(mode_id)
        self.schedule_autosave("tissue_mask")

    def _sync_tissue_mask_status(self):
        current = self._current_atlas_slice()
        if current is None:
            return
        if self.bundle_root is None:
            self.tissue_mask_checkbox.setEnabled(False)
            self.tissue_mask_status.setText(
                "Keep mask: bundle root not found — open a project bundle"
            )
            return
        keep_mask, source = load_keep_mask(self.bundle_root, current.slice_id())
        if keep_mask is None:
            self.tissue_mask_checkbox.setEnabled(False)
            self.tissue_mask_status.setText(
                "Keep mask: not found — run tissue edge cleanup first"
            )
            self.tissue_mask_mode_combo.setVisible(False)
            return
        self.tissue_mask_checkbox.setEnabled(True)
        stats = keep_mask_stats(keep_mask)
        trivial = mask_is_trivial(keep_mask)
        islands = stats.get("n_components", 0)
        if trivial:
            detail = "full keep (background exclusion only)"
        else:
            detail = f"{islands} island(s)"
        self.tissue_mask_status.setText(
            f"Keep mask: {detail}, source={source or 'unknown'}"
        )
        self.tissue_mask_mode_combo.setVisible(
            self.tissue_mask_checkbox.isChecked()
        )

    def _rehydrate_atlas_slices(self, raw_slices: dict) -> None:
        """Rebuild AtlasSlice objects from a pickled dict."""
        rehydrated: dict = {}
        for _, atlas_slice in raw_slices.items():
            old_name = atlas_slice.section_name
            old_x = atlas_slice.x_angle
            old_y = atlas_slice.y_angle
            old_pos = atlas_slice.ap_position
            old_region = atlas_slice.region
            old_hemi = getattr(atlas_slice, "hemisphere", "W")
            old_conf = float(getattr(atlas_slice, "layout_confidence", 1.0))
            old_low = bool(getattr(atlas_slice, "layout_low_confidence", False))
            old_over = bool(getattr(atlas_slice, "layout_overridden", False))
            old_linked = bool(getattr(atlas_slice, "linked", True))
            old_damage = getattr(atlas_slice, "damage_mask", None)
            if old_damage is None and getattr(atlas_slice, "mask", None) is not None:
                old_keep = atlas_slice.mask.astype(np.uint8)
                old_damage = (1 - old_keep).astype(np.uint8)
            old_use_mask = bool(getattr(atlas_slice, "use_tissue_cleanup_mask", False))
            old_warp_mode = getattr(
                atlas_slice, "tissue_mask_warp_mode", WARP_MODE_DEFAULT
            )
            old_keep_source = getattr(atlas_slice, "keep_mask_source", None)

            restored = AtlasSlice(
                old_name,
                old_pos,
                old_x,
                old_y,
                region=old_region,
                hemisphere=old_hemi,
            )
            restored.linked = old_linked
            restored.layout_confidence = old_conf
            restored.layout_low_confidence = old_low
            restored.layout_overridden = old_over
            restored.damage_mask = old_damage
            restored.use_tissue_cleanup_mask = old_use_mask
            restored.tissue_mask_warp_mode = old_warp_mode
            restored.keep_mask_source = old_keep_source
            restored.set_slice(self.atlas, self.annotation)
            self._bind_slice_autosave(restored)
            rehydrated[old_name] = restored
        self.atlas_slices = rehydrated

    def _seed_predicted_delta_from_slices(self) -> None:
        """Estimate inter-section AP spacing from loaded or predicted slices."""
        if self.num_slices < 2:
            return
        positions = [
            float(self.atlas_slices[name].ap_position)
            for name in self.file_list
            if name in self.atlas_slices
        ]
        if len(positions) >= 2:
            self.predicted_delta = float(np.mean(np.diff(positions)))

    def load_alignment(self):
        """Load saved alignment session from the DAPI directory."""
        try:
            tuning_fp = self._tuning_fingerprint()
            result = recover_alignment_session(self.input_path, tuning_fp)
            if result is None:
                return

            self._rehydrate_atlas_slices(result.atlas_slices)
            self.prior_alignment = True
            self._seed_predicted_delta_from_slices()
            restore_nav = result.restore_navigation
            print(
                f"LOG: align_session_loaded source={result.source} "
                f"sections={len(result.atlas_slices)} restore_nav={restore_nav}",
                flush=True,
            )
            print(
                f"Found prior alignment! (source={result.source})",
                flush=True,
            )

            if result.session:
                self._apply_parcellation_state(result.session.get("parcellation"))
                if ap_extrapolation_locked(result.session, self.num_slices):
                    self._ap_locked = True
                    print(
                        "LOG: align_ap_extrapolate locked reason=session_completed",
                        flush=True,
                    )
                if result.restore_navigation:
                    self._session_restore_nav = True
                    max_idx = max(0, self.num_slices - 1)
                    # Always reopen at section 1; stale spinbox + backward nav used to
                    # clobber saved AP when resuming mid-series.
                    self.current_section = 0
                    self.visited = min(
                        int(result.session.get("visited", 0)),
                        max_idx,
                    )

            new_files = set(self.file_list) - set(self.atlas_slices.keys())
            if new_files:
                print("New slices found, re-predicting...", flush=True)
                self.predict_sample_slices()
        except Exception as exc:
            print(f"LOG: align_session_load_failed error={exc}", flush=True)

    def predict_sample_slices(self):
        """Predict the positions of the samples using the tissue predictor"""
        print("Making predictions...", flush=True)
        device = "cuda" if torch.cuda.is_available() else "cpu"
        tissue_predictor = TissuePredictor()
        tissue_predictor.load_state_dict(
            torch.load(self.model_path, map_location=device)
        )
        tissue_predictor.to(device)
        tissue_predictor.eval()

        def restore_label(label, legacy=False):
            pos, x_angle, y_angle = label
            # restore target values
            pos_max = 1324 if not legacy else 528
            pos_min = 0
            pos = pos * (pos_max - pos_min) + pos_min
            x_angle_max = 10
            x_angle_min = -10
            x_angle = x_angle * (x_angle_max - x_angle_min) + x_angle_min
            y_angle_max = 10
            y_angle_min = -10
            y_angle = y_angle * (y_angle_max - y_angle_min) + y_angle_min
            # Quantize the predicted seed to 0.1° — sub-0.1° tilt is below atlas
            # voxel resolution, so the extra precision is meaningless.
            x_angle = round(x_angle, 1)
            y_angle = round(y_angle, 1)
            return [pos, x_angle, y_angle]

        def sobel(image):
            image = cv2.GaussianBlur(image, (3, 3), sigmaX=0, sigmaY=0)
            gX = cv2.Sobel(image, cv2.CV_64F, 1, 0, ksize=3, delta=25)
            gY = cv2.Sobel(image, cv2.CV_64F, 0, 1, ksize=3, delta=25)

            gX = cv2.convertScaleAbs(gX)
            gY = cv2.convertScaleAbs(gY)

            combined = cv2.addWeighted(gX, 0.5, gY, 0.5, 0)
            return combined

        with torch.no_grad():
            x_angles = []
            y_angles = []
            positions = []

            for i in range(self.num_slices):
                # Check if we already loaded a slice with the same name
                if self.file_list[i] in self.atlas_slices.keys():
                    x_angles += [self.atlas_slices[self.file_list[i]].x_angle]
                    y_angles += [self.atlas_slices[self.file_list[i]].y_angle]
                    positions += [self.atlas_slices[self.file_list[i]].ap_position]
                    continue

                sample_path = Path(self.input_path) / self.file_list[i]
                sample_img = cv2.imread(
                    str(sample_path),
                    cv2.IMREAD_GRAYSCALE,
                )
                if sample_img is None:
                    # A missing/corrupt PNG would otherwise raise an opaque
                    # TypeError inside cv2.resize and abort the whole session.
                    print(
                        f"LOG: align_predict_read_failed file={self.file_list[i]}",
                        flush=True,
                    )
                    raise RuntimeError(
                        f"Could not read alignment input image: {sample_path}. "
                        "Ensure 00_dapi has a valid PNG for this slice "
                        "(re-run DAPI cleanup or re-import this section), then "
                        "restart Alignment."
                    )
                # match histogram
                sample_img = cv2.resize(sample_img, (256, 256))
                sample_img = sobel(sample_img)
                sample_img = transforms.ToTensor()(sample_img)
                sample_img = transforms.Normalize(mean=0.1253, std=0.0986)(sample_img)

                sample_img = sample_img.unsqueeze(0)
                sample_img = sample_img.to(device)
                pred = tissue_predictor(sample_img)
                pred = pred.cpu().numpy()

                # restore pred to regular space
                pred = restore_label(pred[0], self.use_legacy)
                x_angles.append(pred[1])
                y_angles.append(pred[2])
                positions.append(pred[0])

            average_x = np.mean(x_angles)
            average_y = np.mean(y_angles)

            if self.num_slices > 1:
                delta_pos = np.mean(np.diff(positions))
            else:
                delta_pos = 0
            self.predicted_delta = delta_pos

            hemi = "W" if self.layout_mode == "whole" else "L"
            force_uniform = self.layout_mode in ("whole", "hemi")

            for i in range(self.num_slices):
                if self.file_list[i] in self.atlas_slices.keys():
                    continue
                sample_path = Path(self.input_path) / self.file_list[i]
                slice_conf = 1.0
                slice_low = False
                if force_uniform:
                    slice_hemi = hemi
                else:
                    detected = detect_tissue_layout(sample_path)
                    slice_hemi = detected.hemisphere
                    slice_conf = detected.confidence
                    slice_low = detected.low_confidence
                    print(
                        "LOG: align_layout_detect "
                        f"slice={self._slice_id_from_filename(self.file_list[i])} "
                        f"layout={detected.layout} hemi={slice_hemi} "
                        f"confidence={detected.confidence:.3f} "
                        f"left_frac={detected.metrics.get('left_frac')} "
                        f"bbox_ratio={detected.metrics.get('bbox_width_ratio')}",
                        flush=True,
                    )

                predicted_slice = AtlasSlice(
                    self.file_list[i],
                    positions[i],
                    average_x,
                    average_y,
                    hemisphere=slice_hemi,
                )
                predicted_slice.layout_confidence = slice_conf
                predicted_slice.layout_low_confidence = slice_low
                predicted_slice.layout_overridden = False

                predicted_slice.set_slice(self.atlas, self.annotation)
                self._bind_slice_autosave(predicted_slice)
                self.atlas_slices[self.file_list[i]] = predicted_slice

        for atlas_slice in self.atlas_slices.values():
            self._bind_slice_autosave(atlas_slice)
        print("LOG: align_predict_complete", flush=True)

    def _find_aspect_constrained_size(self, img1, img2):
        """
        Find one shared display-canvas size for atlas and DAPI.

        The canvas does not itself change an image's aspect ratio.  Each image
        is fitted into it by :meth:`_fit_image_to_display_canvas`; unused
        margins are black.  Keeping that distinction avoids stretching either
        image merely to make their array shapes equal.

        Parameters:
        - img1: First image (assumed to be a NumPy array or similar with shape (height, width)).
        - img2: Second image (assumed to be a NumPy array or similar with shape (height, width)).

        Returns:
        - Tuple (width, height): Ideal dimensions to resize both images to.
        """

        def calculate_target_size(img):
            height, width = img.shape[:2]
            aspect_ratio = width / height
            if height > width:
                # Height is the larger dimension
                target_height = max(height, 1080)
                target_width = int(target_height * aspect_ratio)
            else:
                # Width is the larger dimension
                target_width = max(width, 1080)
                target_height = int(target_width / aspect_ratio)
            return target_width, target_height

        target_size_img1 = calculate_target_size(img1)
        target_size_img2 = calculate_target_size(img2)

        # The target size should be the max width and max height obtained from the two images
        target_width = max(target_size_img1[0], target_size_img2[0])
        target_height = max(target_size_img1[1], target_size_img2[1])

        return (target_width, target_height)

    @staticmethod
    def _fit_image_to_display_canvas(image, canvas_size, *, anchor_left=False):
        """Return *image* centred in a zero-padded shared canvas.

        ``canvas_size`` is ``(width, height)``.  The image is scaled uniformly
        to fit within it, preserving its aspect ratio, then placed at the
        centre.  A cropped left-hemisphere atlas can instead be anchored to
        the left edge so it remains in the same half of the DAPI field. Both
        Napari layers therefore share pixel coordinates without the
        display-only resize distorting DAPI or atlas geometry.
        """
        canvas_width, canvas_height = canvas_size
        height, width = image.shape[:2]
        if height <= 0 or width <= 0:
            raise ValueError("Cannot fit an empty image to the alignment canvas")

        scale = min(canvas_width / width, canvas_height / height)
        fitted_width = max(1, min(canvas_width, round(width * scale)))
        fitted_height = max(1, min(canvas_height, round(height * scale)))
        interpolation = (
            cv2.INTER_AREA if scale < 1 else cv2.INTER_LINEAR
        )
        fitted = cv2.resize(image, (fitted_width, fitted_height), interpolation=interpolation)

        canvas = np.zeros((canvas_height, canvas_width), dtype=image.dtype)
        top = (canvas_height - fitted_height) // 2
        left = 0 if anchor_left else (canvas_width - fitted_width) // 2
        canvas[top : top + fitted_height, left : left + fitted_width] = fitted
        return canvas

    def _align_seam_meta_dir(self):
        """Find the project metadata folder used for seamgrid sidecars."""
        seen = set()
        starts = [Path(self.input_path)]
        if self.bundle_root is not None:
            starts.insert(0, Path(self.bundle_root))
        for start in starts:
            for root in (start,) + tuple(start.parents):
                if root in seen:
                    continue
                seen.add(root)
                candidate = root / ".masonjar"
                if candidate.is_dir():
                    return candidate
        return None

    def _resolve_align_seam_preview(self, primary: Path, filename: str) -> Path:
        """Return a cached live seam-corrected display image when possible."""
        try:
            stamp = primary.stat().st_mtime_ns
            cache_key = f"{primary.resolve()}:{stamp}"
            cached = self._align_seam_display_cache.get(cache_key)
            if cached is not None and cached.is_file():
                return cached

            import seam_correct

            digest = hashlib.sha256(cache_key.encode("utf-8")).hexdigest()
            destination = Path(self._align_seam_display_tempdir.name) / f"{digest}.png"
            geometry_path = Path(self.input_path) / filename
            if not geometry_path.is_file():
                geometry_path = primary
            info = seam_correct.correct_file_to(
                destination,
                primary,
                band=seam_correct.DEFAULT_BAND,
                meta_dir=self._align_seam_meta_dir(),
                seam_mode="auto",
                seamgrid_slice_id=Path(filename).stem,
                geometry_image_path=geometry_path,
            )
            self._align_seam_display_cache[cache_key] = destination
            print(
                "LOG: align_seam_preview "
                f"slice={Path(filename).stem} mode={info.get('mode')} "
                f"n_seams={info.get('n_seams')}",
                flush=True,
            )
            return destination
        except Exception as exc:  # noqa: BLE001
            print(f"LOG: align_seam_preview_failed {exc!r}", flush=True)
            return primary

    def _resolve_align_display_dapi(self, filename: str):
        """Use live seam correction for the display only when requested."""
        primary = Path(self.input_path) / filename
        toggle = getattr(self, "seam_display_toggle", None)
        if toggle is None or not toggle.isChecked():
            return primary
        return self._resolve_align_seam_preview(primary, filename)

    def _load_dapi_display_cached(self, display_path):
        """Load a DAPI display image, caching by path to avoid re-reading on
        revisit. The cached array is never mutated in place (callers resize into
        a new array), so returning the shared reference is safe."""
        key = str(display_path)
        img = self._dapi_img_cache.get(key)
        if img is None:
            img = cv2.imread(key, cv2.IMREAD_GRAYSCALE)
            if img is not None:
                self._dapi_img_cache[key] = img
        return img

    def update_display(self):
        """Update the viewer to current section"""
        _t0 = time.perf_counter()
        # Do NOT toggle grid off/on each navigation: toggling grid.enabled
        # forces napari to rebuild the whole grid layout (~0.6-0.9s each way,
        # the dominant slice-nav cost). Layers are a fixed pair, so the grid
        # only needs enabling once (below); updating layer .data refreshes the
        # view on its own.
        _t_gridoff = time.perf_counter()

        display_path = self._resolve_align_display_dapi(
            self.file_list[self.current_section]
        )
        _dbg_dapi_cached = str(display_path) in self._dapi_img_cache
        sample_img = self._load_dapi_display_cached(display_path)
        _t_dapi = time.perf_counter()
        _dbg_dapi_shape = getattr(sample_img, "shape", None)

        new_size = self._find_aspect_constrained_size(
            sample_img,
            self.atlas_slices[self.file_list[self.current_section]].image,
        )
        sample_img = self._fit_image_to_display_canvas(sample_img, new_size)
        _t_resize1 = time.perf_counter()

        self.tissue_layer.data = sample_img
        _t_tissue = time.perf_counter()
        # resize atlas to match tissue
        _dbg_cur = self.atlas_slices[self.file_list[self.current_section]]
        _dbg_atlas_cached = getattr(_dbg_cur, "_slice_cache_key", None) == (
            _dbg_cur.ap_position,
            _dbg_cur.x_angle,
            _dbg_cur.y_angle,
            _dbg_cur.hemisphere,
        )
        _t_pre_slice = time.perf_counter()
        self.atlas_slices[self.file_list[self.current_section]].set_slice(
            self.atlas, self.annotation
        )
        _t_slice = time.perf_counter()

        temp_data = self._fit_image_to_display_canvas(
            self.atlas_slices[self.file_list[self.current_section]].image,
            new_size,
            anchor_left=(
                self.atlas_slices[self.file_list[self.current_section]].hemisphere
                == "L"
            ),
        )
        _t_resize2 = time.perf_counter()
        self.atlas_layer.data = temp_data
        _t_atlas = time.perf_counter()
        # Enable the grid only when it is not already on (first navigation).
        # Assigning the same value would still fire napari's relayout, so guard
        # it to keep subsequent navigations cheap.
        if not self.viewer.grid.enabled:
            self.viewer.grid.enabled = True
        _t_gridon = time.perf_counter()

        # Set linkage
        self.link_angles_button.setChecked(
            self.atlas_slices[self.file_list[self.current_section]].linked
        )

        current = self.atlas_slices[self.file_list[self.current_section]]
        self.x_angle_spinbox.blockSignals(True)
        self.y_angle_spinbox.blockSignals(True)
        self.ap_position_spinbox.blockSignals(True)
        self.region_selection.blockSignals(True)
        self.x_angle_spinbox.setValue(current.x_angle)
        self.y_angle_spinbox.setValue(current.y_angle)
        self.ap_position_spinbox.setValue(current.ap_position)
        self.region_selection.setCurrentIndex(
            list(self.region_tags.values()).index(current.region)
        )
        self.x_angle_spinbox.blockSignals(False)
        self.y_angle_spinbox.blockSignals(False)
        self.ap_position_spinbox.blockSignals(False)
        self.region_selection.blockSignals(False)
        self._sync_layout_selection_from_slice()

        self.tissue_mask_checkbox.blockSignals(True)
        self.tissue_mask_checkbox.setChecked(bool(current.use_tissue_cleanup_mask))
        self.tissue_mask_checkbox.blockSignals(False)
        mode_idx = warp_mode_index(
            current.tissue_mask_warp_mode or WARP_MODE_DEFAULT
        )
        self.tissue_mask_mode_combo.blockSignals(True)
        self.tissue_mask_mode_combo.setCurrentIndex(mode_idx)
        self.tissue_mask_mode_combo.blockSignals(False)
        self._sync_tissue_mask_status()

        self.update_section_header()
        self._controls_seeded = True
        _t_end = time.perf_counter()
        # TEMP fine-grained timing to isolate the napari render cost per phase.
        # Remove once the bottleneck line is confirmed.
        print(
            "LOG: align_nav_timing "
            f"gridoff={(_t_gridoff - _t0) * 1000:.0f} "
            f"dapi={(_t_dapi - _t_gridoff) * 1000:.0f}"
            f"(cached={_dbg_dapi_cached},shape={_dbg_dapi_shape}) "
            f"resize1={(_t_resize1 - _t_dapi) * 1000:.0f} "
            f"tissuelayer={(_t_tissue - _t_resize1) * 1000:.0f} "
            f"set_slice={(_t_slice - _t_pre_slice) * 1000:.0f}"
            f"(cached={_dbg_atlas_cached}) "
            f"resize2={(_t_resize2 - _t_slice) * 1000:.0f} "
            f"atlaslayer={(_t_atlas - _t_resize2) * 1000:.0f} "
            f"gridon={(_t_gridon - _t_atlas) * 1000:.0f} "
            f"ui={(_t_end - _t_gridon) * 1000:.0f} "
            f"total={(_t_end - _t0) * 1000:.0f}ms",
            flush=True,
        )

    def _sync_layout_selection_from_slice(self):
        if not self.file_list or self.num_slices == 0:
            return
        current = self.atlas_slices[self.file_list[self.current_section]]
        hemi = getattr(current, "hemisphere", "W")
        label = "Left hemisphere" if hemi == "L" else "Whole brain"
        self.layout_selection.blockSignals(True)
        idx = list(self.layout_tags.keys()).index(label)
        self.layout_selection.setCurrentIndex(idx)
        self.layout_selection.blockSignals(False)

    def update_linkage(self):
        """Update the linkage of the current slice"""
        self.atlas_slices[self.file_list[self.current_section]].linked = (
            self.link_angles_button.isChecked()
        )

    def set_all_angles(self):
        """Update linked slices' angles without redrawing the view."""
        # Check if current slice is linked
        current_slice = self.atlas_slices[self.file_list[self.current_section]]
        if current_slice.linked:
            for this_slice in self.atlas_slices.values():
                if this_slice.linked:
                    this_slice.x_angle = self.x_angle_spinbox.value()
                    this_slice.y_angle = self.y_angle_spinbox.value()

    def _on_debounce_changed(self, value):
        """Live-adjust the re-slice debounce delay (ms) from the Options dock."""
        try:
            self._update_debounce_ms = max(0, int(round(value)))
        except (TypeError, ValueError):
            self._update_debounce_ms = 500

    def que_update_slice(self):
        """Coalesce rapid angle changes into one display update."""
        if self.slice_update_timer.isActive():
            self.slice_update_timer.stop()
        self.slice_update_timer.start(self._update_debounce_ms)

    def que_update_layout(self):
        if self.slice_update_timer.isActive():
            self.slice_update_timer.stop()
        self.slice_update_timer.singleShot(self._update_debounce_ms, self.update_layout)

    def que_update_position(self):
        """Coalesce rapid AP changes into one display update."""
        if self.pos_update_timer.isActive():
            self.pos_update_timer.stop()
        self.pos_update_timer.start(self._update_debounce_ms)

    def update_slice(self):
        """Update the angles and region of the current slice"""
        current_slice = self.atlas_slices[self.file_list[self.current_section]]
        current_slice.x_angle = self.x_angle_spinbox.value()
        current_slice.y_angle = self.y_angle_spinbox.value()
        current_slice.region = self.region_tags[self.region_selection.currentText()]
        self.set_all_angles()
        self.update_display()
        # No autosave here: angle edits are committed to the in-memory slice
        # and persisted when leaving the section (next/previous) or on close,
        # which is the final value. Avoids a NAS write per settled edit.

    def update_region(self):
        """Store the region choice for the current slice.

        Region (Cerebrum / No Cerebrum) does not affect the atlas preview
        slice (only AP/angles/hemisphere do); it is consumed later at
        registration via region_code. So skip set_slice / set_all_angles /
        update_display entirely and just persist the value — the click
        stays instant instead of triggering a debounced redraw + autosave.
        """
        current_slice = self.atlas_slices[self.file_list[self.current_section]]
        current_slice.region = self.region_tags[self.region_selection.currentText()]
        self._flush_autosave("edit")

    def update_layout(self):
        """Update the tissue layout (hemisphere) of the current slice."""
        current_slice = self.atlas_slices[self.file_list[self.current_section]]
        label = self.layout_selection.currentText()
        current_slice.hemisphere = self.layout_tags[label]
        current_slice.layout_overridden = True
        current_slice.layout_low_confidence = False
        current_slice.set_slice(self.atlas, self.annotation)
        self.update_display()
        self._flush_autosave("edit")

    def apply_layout_to_all(self):
        """Apply the current 'Section layout' choice to every section.

        Only the attribute is written for each slice; the heavy atlas
        re-slice is deferred and happens lazily when a section is next
        displayed (update_display() calls set_slice(), whose cache key
        includes hemisphere, so a changed hemisphere forces a recompute).
        The current section is recomputed immediately for visual feedback.
        """
        label = self.layout_selection.currentText()
        if label not in self.layout_tags:
            return
        target = self.layout_tags[label]
        reply = QMessageBox.question(
            None,
            "Apply layout to all sections",
            f"Set all {self.num_slices} sections to '{label}'?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return
        for atlas_slice in self.atlas_slices.values():
            atlas_slice.hemisphere = target
            atlas_slice.layout_overridden = True
            atlas_slice.layout_low_confidence = False
        current_slice = self.atlas_slices[self.file_list[self.current_section]]
        current_slice.set_slice(self.atlas, self.annotation)
        self.update_display()
        self._flush_autosave("edit")

    def update_position(self):
        """Update the position of the current slice"""
        current_slice = self.atlas_slices[self.file_list[self.current_section]]
        current_slice.ap_position = self.ap_position_spinbox.value()
        self.update_display()
        # No autosave here: AP edits persist when leaving the section or on
        # close (the final value), avoiding a NAS write per settled edit.

    def adjust_positions(self):
        """Extrapolate AP for unconfirmed sections from tuned (confirmed) prefix."""
        if getattr(self, "_ap_locked", False):
            print(
                "LOG: align_ap_extrapolate skipped reason=ap_locked",
                flush=True,
            )
            return
        if self.current_section >= self.num_slices - 1:
            return
        # Callers must sync the slice being confirmed before advancing; do not sync
        # here — after next_section the spinboxes still show the prior slice.
        # ``current_section`` is the slice now being viewed; confirmed tuning is
        # indices ``0 .. current_section - 1`` only (exclude model AP on current).

        n_confirm = self.current_section
        if n_confirm < 2:
            return

        confirmed = [
            self.atlas_slices[self.file_list[i]].ap_position for i in range(n_confirm)
        ]
        max_ap = 528 if self.use_legacy else 1319
        spacing_floor = int(self.spacing) // 10 if self.spacing is not None else 0
        model_delta = (
            float(self.predicted_delta)
            if self.predicted_delta is not None
            else None
        )
        updates = extrapolate_ap_positions(
            confirmed,
            self.num_slices,
            max_ap=max_ap,
            model_delta=model_delta,
        )
        if not updates:
            return

        delta = confirmed[-1] - confirmed[-2] if len(confirmed) >= 2 else 0
        print(
            f"LOG: align_ap_extrapolate from={n_confirm} to={self.num_slices - 1} "
            f"delta={delta:.1f}",
            flush=True,
        )

        for idx, ap in updates:
            # Only seed sections the user has not reached yet. Any section at or
            # before ``visited`` may have been manually tuned, so extrapolation
            # must never overwrite it (preserves user-entered AP values).
            if idx <= self.visited:
                continue
            if spacing_floor > 0:
                ap = max(ap, spacing_floor)
            self.atlas_slices[self.file_list[idx]].ap_position = int(ap)

    def next_section(self):
        """Move to next section"""
        if self.current_section < self.num_slices - 1:
            self._sync_current_slice_from_controls()
            prev_index = self.current_section
            self.current_section += 1
            self.visited = max(self.visited, self.current_section)
            self.progress_bar.setValue(self.current_section + 1)
            self.progress_bar.setFormat(
                f"{self.current_section + 1} / {self.num_slices}"
            )
            if self._forced_ap_enabled():
                self._apply_forced_ap_step(prev_index)
            else:
                self.adjust_positions()
            self.update_display()
            self.schedule_autosave("next_section")
            self._focus_ap_for_edit()

    def previous_section(self):
        """Move to previous section"""
        if self.current_section > 0:
            self._sync_current_slice_from_controls()
            self.current_section -= 1
            self.progress_bar.setValue(self.current_section + 1)
            self.progress_bar.setFormat(
                f"{self.current_section + 1} / {self.num_slices}"
            )
            self.update_display()
            self.schedule_autosave("previous_section")
            self._focus_ap_for_edit()

    def _forced_ap_enabled(self):
        cb = getattr(self, "forced_ap_checkbox", None)
        try:
            return bool(cb is not None and cb.isChecked())
        except Exception:
            return False

    def _apply_forced_ap_step(self, prev_index):
        """Forced extrapolation: overwrite the just-entered section's AP with the
        previous section's AP plus the configured step (positive spacing)."""
        try:
            step = int(round(float(self.forced_ap_spinbox.value())))
        except Exception:
            step = 0
        if step <= 0:
            return
        try:
            max_ap = 528 if self.use_legacy else 1319
            prev_ap = int(self.atlas_slices[self.file_list[prev_index]].ap_position)
            new_ap = max(0, min(max_ap, prev_ap + step))
            self.atlas_slices[self.file_list[self.current_section]].ap_position = new_ap
            print(
                f"LOG: align_ap_forced prev_idx={prev_index} "
                f"to_idx={self.current_section} step={step} new_ap={new_ap}",
                flush=True,
            )
        except Exception as exc:
            print(f"LOG: align_ap_forced error {exc}", flush=True)

    def _focus_ap_for_edit(self):
        """After navigating, focus the AP spinbox with its value selected so the
        experimenter can immediately type a new AP position over it."""

        def _do():
            try:
                self.ap_position_spinbox.setFocus()
                self.ap_position_spinbox.selectAll()
            except Exception:
                pass

        try:
            # Defer so it runs after update_display/repaint and the click event.
            QTimer.singleShot(0, _do)
        except Exception:
            _do()

    def prompt_goto_section(self):
        """Ask for a 1-based section number and jump straight to it."""
        if self.num_slices <= 1:
            return
        target, ok = QInputDialog.getInt(
            None,
            "Go to section",
            f"Section number (1 - {self.num_slices}):",
            self.current_section + 1,
            1,
            self.num_slices,
            1,
        )
        if ok:
            self.goto_section(target - 1)

    def goto_section(self, index):
        """Jump directly to a section by 0-based index (view only).

        Unlike next_section this does not run adjust_positions (AP
        extrapolation), which assumes sequential confirmation from the start;
        jumping only changes which section is shown.
        """
        if index < 0 or index >= self.num_slices or index == self.current_section:
            return
        self._sync_current_slice_from_controls()
        self.current_section = index
        self.visited = max(self.visited, self.current_section)
        self.progress_bar.setValue(self.current_section + 1)
        self.progress_bar.setFormat(
            f"{self.current_section + 1} / {self.num_slices}"
        )
        self.update_display()
        self.schedule_autosave("goto_section")
        self._focus_ap_for_edit()

    def isolate_section(self, sample):
        """
        Use SAM to allow the user to isolate each section in the image
        Args:
            sample: image to isolate (gray scale, uint8)
        """

        # Load SAM model
        sam = sam_model_registry["vit_b"](checkpoint=self.sam_path)

        # Check for CUDA or MPS and move the model to the appropriate device
        if torch.cuda.is_available():
            sam = sam.to(device="cuda")
        elif torch.backends.mps.is_available():
            sam = sam.to(device="mps")

        # Set the image for SAM
        predictor = SamPredictor(sam)
        sample_image = cv2.cvtColor(sample.copy(), cv2.COLOR_GRAY2BGR)
        predictor.set_image(sample_image)

        # Prepare for point selection
        points = [[sample.shape[1] // 2, sample.shape[0] // 2]]

        # def get_point(event, x, y, flags, param):
        #     if event == cv2.EVENT_LBUTTONDOWN:
        #         if len(points) < 3:  # Limit to 3 points
        #             points.append([x, y])
        #             cv2.circle(sample_image, (x, y), 3, (0, 0, 255), -1)
        #             cv2.imshow(f"Point Selector", sample_image)

        # # Create a window to display the image and set the mouse callback
        # cv2.namedWindow("Section Isolation")
        # cv2.setMouseCallback("Section Isolation", get_point)
        # cv2.imshow("Section Isolation", sample_image)
        # cv2.waitKey(0)
        # cv2.destroyWindow("Section Isolation")

        if len(points) > 0:
            # Convert points to numpy array
            points_np = np.array(points)

            # Generate mask using SAM
            masks, _, _ = predictor.predict(points_np, np.array([1] * len(points_np)))

            # Display the generated mask for confirmation
            mask = masks[0]  # Assuming the first mask is the most relevant
            return (sample * mask.astype(np.uint8))
            # mask_display = mask.astype(np.uint8) * 255
            # # convert to color
            # mask_display = cv2.applyColorMap(mask_display, cv2.COLORMAP_JET)
            # composite = cv2.addWeighted(sample_image, 0.5, mask_display, 0.5, 0)
            # cv2.imshow("Generated Mask (Press 'y' to confirm 'n' to cancel)", composite)
            # key = cv2.waitKey(0)
            # if key == ord('y'):  # User confirms the mask
            #     cv2.destroyAllWindows()
            #     return sample * mask.astype(np.uint8)
            # elif key == ord('n'):  # User cancels the mask
            #     cv2.destroyAllWindows()
            #     self.isolate_section(sample)

    def _confirm_finish(self) -> bool:
        """Ask before save+warp so accidental Finish clicks are easy to cancel."""
        parent = None
        try:
            parent = resolve_napari_qt_window(self.viewer)
        except Exception:
            parent = None
        return confirm_align_finish(parent)

    def finish(self):
        """Finish alignment"""
        if not self._confirm_finish():
            return
        self._session_finished = True
        # disconnect signals
        self.x_angle_spinbox.valueChanged.disconnect(self.que_update_slice)
        self.y_angle_spinbox.valueChanged.disconnect(self.que_update_slice)
        self.ap_position_spinbox.valueChanged.disconnect(self.que_update_position)
        self.region_selection.currentIndexChanged.disconnect(self.update_region)
        self.layout_selection.currentIndexChanged.disconnect(self.que_update_layout)
        self.next_button.clicked.disconnect(self.next_section)
        self.previous_button.clicked.disconnect(self.previous_section)
        self.finish_button.clicked.disconnect(self.finish)

        # Save synchronously before closing Napari so a completed warp always
        # has a recoverable set of tuning values.
        with perf_log.perf_section("align.finish.save_alignment"):
            self.save_alignment()

        # Hand Mason Jar back immediately so users see warp progress (not a
        # minimized window). Closing Napari is safe: _session_finished skips
        # the cancel / Viewer-closed handshake.
        print("ALIGN_WARPING", flush=True)
        try:
            self.viewer.close()
        except Exception as exc:
            print(f"LOG: align_viewer_close_after_finish error={exc}", flush=True)

        # warp images
        emit_log("Warping images…")
        emit_progress_phase(0, "Warping images…")
        with perf_log.perf_section("align.finish.load_structure_map"):
            with open(self.structures_path, "rb") as f:
                structure_map = pickle.load(f)

        from slice_index import slice_stem_from_image_filename

        warp_ok = []
        warp_failed = []
        slice_warp_masks = {}
        multi_region_modes = {
            WARP_MODE_HYBRID,
            WARP_MODE_REGION_DUAL,
            WARP_MODE_PER_ISLAND,
        }
        warped_at = datetime.now(timezone.utc).isoformat()
        n_slices = max(1, self.num_slices)
        for i in range(self.num_slices):
            filename = self.file_list[i]
            slice_stem = slice_stem_from_image_filename(filename)
            pct = int(((i + 1) / n_slices) * 100)
            status = f"Warping {i + 1}/{self.num_slices} {filename}"
            emit_log(status)
            emit_progress_phase(pct, status)
            current_slice = self.atlas_slices[filename]
            with perf_log.perf_section(f"align.finish.read_input.{slice_stem}"):
                sample = cv2.imread(
                    str(Path(self.input_path) / filename),
                    cv2.IMREAD_GRAYSCALE,
                )

            use_mask = bool(getattr(current_slice, "use_tissue_cleanup_mask", False))
            warp_mode = getattr(
                current_slice, "tissue_mask_warp_mode", WARP_MODE_DEFAULT
            )
            skip_region_prefilter = use_mask and warp_mode in multi_region_modes

            atlas_image = current_slice.image
            atlas_label = current_slice.label
            if current_slice.region != "A" and not skip_region_prefilter:
                atlas_image, atlas_label = mask_slice_by_region(
                    current_slice.image,
                    current_slice.label,
                    structure_map,
                    current_slice.region,
                )

            saved_image = current_slice.image
            saved_label = current_slice.label
            warp_meta = None
            try:
                current_slice.image = atlas_image
                current_slice.label = atlas_label
                with perf_log.perf_section(
                    f"align.finish.register.{slice_stem}"
                ):
                    (
                        warped_labels,
                        warped_atlas,
                        color_label,
                        warp_meta,
                    ) = current_slice.get_registered(
                        sample,
                        self.structures_path,
                        bundle_root=self.bundle_root,
                        structure_map=structure_map,
                    )
            except Exception as exc:
                err_msg = str(exc)
                warp_failed.append(
                    {
                        "slice_id": slice_stem,
                        "file": filename,
                        "error": err_msg,
                        "tissue_mask_warp_mode": warp_mode if use_mask else "standard",
                    }
                )
                print(
                    f"LOG: align_warp_failed slice={slice_stem} file={filename} error={err_msg}",
                    flush=True,
                )
                continue
            finally:
                current_slice.image = saved_image
                current_slice.label = saved_label

            if warp_meta:
                record = {
                    **warp_meta,
                    "warped_at": warped_at,
                }
                slice_warp_masks[slice_stem] = record
                append_alignment_mask_log(
                    Path(self.output_path),
                    {
                        "slice_id": slice_stem,
                        "align_output_rel": str(Path(self.output_path)),
                        **warp_meta,
                    },
                )

            from annotation_relabel import (
                colorize_labels,
                ensure_full_backup,
                relabel_to_target,
                set_slice_parcellation,
            )
            from structure_catalog import FULL_DETAIL_TIER

            output_leaf = Path(self.output_path)
            with perf_log.perf_section(f"align.finish.parcellation.{slice_stem}"):
                ensure_full_backup(output_leaf, slice_stem, warped_labels)

                tier_id, st_level = self._parcel_target()
                if self.catalog and tier_id != FULL_DETAIL_TIER:
                    result = relabel_to_target(
                        warped_labels,
                        self.catalog,
                        tier_id=tier_id,
                        st_level=st_level,
                        structure_map=structure_map,
                    )
                    warped_labels = result.label_array
                    color_label = colorize_labels(warped_labels, structure_map)
                    set_slice_parcellation(
                        output_leaf,
                        slice_stem,
                        tier_id=tier_id,
                        st_level=st_level,
                    )
                    print(
                        f"LOG: align_parcellation slice={slice_stem} "
                        f"tier={tier_id} level={st_level} "
                        f"pixels_changed={result.pixels_changed}",
                        flush=True,
                    )

            stripped_filename = filename.split(".")
            stripped_filename = ".".join(stripped_filename[:-1])

            with perf_log.perf_section(f"align.finish.write_outputs.{slice_stem}"):
                cv2.imwrite(
                    str(Path(self.output_path) / f"Atlas_{stripped_filename}.png"),
                    warped_atlas,
                )
                color_label = add_outlines(warped_labels, color_label)
                # make label rgb
                color_label = cv2.cvtColor(color_label, cv2.COLOR_BGR2RGB)
                cv2.imwrite(
                    str(Path(self.output_path) / f"Label_{stripped_filename}.png"),
                    color_label,
                )

                # convert sample to color
                sample = cv2.cvtColor(sample, cv2.COLOR_GRAY2RGB)
                # composite image
                composite = cv2.addWeighted(
                    sample,
                    0.80,
                    color_label,
                    0.20,
                    0,
                )

                cv2.imwrite(
                    str(Path(self.output_path) / f"Composite_{stripped_filename}.png"),
                    composite,
                )

                with open(
                    Path(self.output_path) / f"Annotation_{stripped_filename}.pkl", "wb"
                ) as f:
                    pickle.dump(warped_labels, f)
            warp_ok.append(slice_stem)

        from run_manifest import write_run_manifest

        manifest_payload = {
            "step": "align",
            "input_dir": self.input_path,
            "output_dir": self.output_path,
            "layout_mode": self.layout_mode,
            "whole": self.layout_mode == "whole",
            "spacing": self.spacing,
            "legacy": self.use_legacy,
            "slice_filter": sorted(self.slice_filter)
            if self.slice_filter is not None
            else None,
            "warp_ok": warp_ok,
            "warp_failed": warp_failed,
            "slice_layouts": {},
            "slice_warp_masks": slice_warp_masks,
        }
        for section_name, atlas_slice in self.atlas_slices.items():
            slice_id = self._slice_id_from_filename(section_name)
            manifest_payload["slice_layouts"][slice_id] = {
                "hemisphere": getattr(atlas_slice, "hemisphere", "W"),
                "layout_confidence": float(
                    getattr(atlas_slice, "layout_confidence", 1.0)
                ),
                "layout_low_confidence": bool(
                    getattr(atlas_slice, "layout_low_confidence", False)
                ),
                "layout_overridden": bool(
                    getattr(atlas_slice, "layout_overridden", False)
                ),
            }
        with perf_log.perf_section("align.finish.write_manifest"):
            write_run_manifest(self.output_path, manifest_payload)

            report_dir = Path(self.output_path) / ".masonjar"
            report_dir.mkdir(parents=True, exist_ok=True)
            report_path = report_dir / "align_warp_report.json"
            with open(report_path, "w", encoding="utf-8") as report_file:
                json.dump(manifest_payload, report_file, indent=2)

        failed_ids = [
            str(row.get("slice_id") or row.get("file") or "")
            for row in warp_failed
        ]
        summary = {
            "ok": True,
            "warped": len(warp_ok),
            "failed": len(warp_failed),
            "failed_slice_ids": [sid for sid in failed_ids if sid],
            "output_dir": str(self.output_path),
        }
        if warp_failed:
            print(
                f"LOG: align_warp_summary ok={len(warp_ok)} failed={len(warp_failed)}",
                flush=True,
            )
        if not warp_ok:
            summary["ok"] = False
            emit_result(summary)
            print("LOG: align_warp_zero_slices_warped", flush=True)
            raise SystemExit(1)
        try:
            mark_session_completed(self.input_path, self._tuning_fingerprint())
        except Exception as exc:
            print(f"LOG: align_session_complete_failed error={exc}", flush=True)
        emit_progress_phase(100, "Warping complete")
        emit_result(summary)
        print("Done!", flush=True)

    def _emit_viewer_closed_handshake(self) -> None:
        if self._viewer_close_handshake_sent:
            return
        self._viewer_close_handshake_sent = True
        print("LOG: align_viewer_closed", flush=True)
        print("Viewer closed", flush=True)

    def _close_viewer_session(self, reason: str) -> None:
        if self._session_finished:
            return
        self._flush_autosave(reason)
        self._emit_viewer_closed_handshake()

    def _request_viewer_exit(self, reason: str) -> None:
        if self._session_finished:
            return
        self._close_viewer_session(reason)
        try:
            if self._save_exit_flag.is_file():
                self._save_exit_flag.unlink()
        except OSError:
            pass
        try:
            app = QApplication.instance()
            if app is not None:
                app.quit()
        except Exception:
            pass

    def _poll_save_exit(self) -> None:
        if self._session_finished:
            return
        try:
            if self._save_exit_flag.is_file():
                self._request_viewer_exit("cancel")
        except OSError:
            pass

    def _bind_save_exit_poll(self) -> None:
        try:
            if self._save_exit_flag.is_file():
                self._save_exit_flag.unlink()
        except OSError:
            pass
        self._save_exit_timer.timeout.connect(self._poll_save_exit)
        self._save_exit_timer.start(200)

    def _bind_viewer_close_flush(self) -> None:
        try:
            qt_window = self.viewer.window._qt_window
            if qt_window is None:
                return

            controller = self

            class _CloseFilter(QtCore.QObject):
                def eventFilter(self, obj, event):
                    if event.type() == QtCore.QEvent.Close:
                        if not controller._session_finished:
                            controller._close_viewer_session("window_close")
                    return False

            self._close_filter = _CloseFilter(qt_window)
            qt_window.installEventFilter(self._close_filter)
        except Exception:
            pass

    def start_viewer(self):
        """Start the viewer"""
        self.viewer.show()
        self._show_align_chrome()
        self.update_display()
        show_napari_maximized_and_activate(self.viewer)
        self._schedule_default_options_width()
        QTimer.singleShot(300, self._initialize_atlas_zoom_policy)
        self._bind_viewer_close_flush()
        self._bind_save_exit_poll()
        napari.run()
        self._save_exit_timer.stop()
        if not self._session_finished and not self._viewer_close_handshake_sent:
            self._close_viewer_session("viewer_close")


if __name__ == "__main__":
    import perf_log
    perf_log.perf_start_total("align")
    parser = argparse.ArgumentParser(description="Map sections to atlas space")
    parser.add_argument(
        "-o",
        "--output",
        help="output directory, only use if graphical false",
        default="",
    )
    parser.add_argument(
        "-i", "--input", help="input directory, only use if graphical false", default=""
    )
    parser.add_argument("-m", "--model", default="../models/predictor_encoder.pt")
    parser.add_argument("-s", "--sam", default="~/.belljar/models/sam_vit_b.pth")
    parser.add_argument("-n", "--nrrd", help="path to nrrd files", default="")
    parser.add_argument("-w", "--whole", default=False)
    parser.add_argument(
        "-a", "--spacing", help="override predicted spacing", default=False
    )
    parser.add_argument("-l", "--legacy", help="use legacy atlas", default=False)
    parser.add_argument("-c", "--map", help="map file", default="../csv/class_map.pkl")
    parser.add_argument(
        "--slice-list",
        help="JSON file with slice ids to process",
        default="",
    )
    parser.add_argument(
        "-b",
        "--bundle",
        help="Mason Jar bundle root (for tissue cleanup mask lookup)",
        default="",
    )
    args = parser.parse_args()

    from slice_index import load_slice_list

    slice_filter = load_slice_list(args.slice_list.strip() or None)

    align_controller = AlignmentController(
        nrrd_path=args.nrrd.strip(),
        input_path=args.input.strip(),
        output_path=args.output.strip(),
        structures_path=args.map.strip(),
        model_path=args.model.strip(),
        sam_path=args.sam.strip(),
        spacing=args.spacing if args.spacing else None,
        layout_mode=parse_layout_mode(str(args.whole)),
        use_legacy=args.legacy.strip().lower() == "true",
        slice_filter=slice_filter,
        bundle_root=args.bundle.strip() or None,
    )
