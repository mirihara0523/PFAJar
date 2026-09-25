import numpy as np
import cv2
import argparse
import os, sys
import pickle
import hashlib
import tempfile
import perf_log
from concurrent.futures import ThreadPoolExecutor
from adjust_raster import EditableRaster
from functools import lru_cache
from pathlib import Path
from qtpy.QtWidgets import (
    QApplication,
    QMainWindow,
    QGraphicsView,
    QGraphicsScene,
    QGraphicsEllipseItem,
    QVBoxLayout,
    QPushButton,
    QInputDialog,
    QHBoxLayout,
    QWidget,
    QLabel,
    QSlider,
    QStatusBar,
    QCheckBox,
    QMessageBox,
    QLineEdit,
    QListWidget,
    QComboBox,
    QCompleter,
    QGroupBox,
    QSpinBox,
    QColorDialog,
    QProgressDialog,
    QScrollArea,
    QToolButton,
    QListWidgetItem,
    QFrame,
    QToolBar,
    QDockWidget,
    QSizePolicy,
    QShortcut,
    QSplitter,
)
from qtpy.QtGui import QImage, QPixmap, QPainter, QColor, QPen, QBrush, QKeySequence, QTransform, QKeyEvent, QCursor, QDrag
from qtpy.QtCore import Qt, QPoint, QPointF, QEvent, QTimer, QRect, QRectF, QSettings, QMimeData
from dialog_preferences import (
    KEY_CONFIRM_SAVE_OVERWRITE,
    KEY_ISOLATE_LABEL_AUDIT,
    KEY_MIXED_RESOLUTION_TIER,
    is_suppressed,
    set_suppressed,
)
from slice_atlas import add_outlines
from adjust_channels import (
    build_lowres_channel_index,
    lowres_channels_for_slice,
    resolve_previews_dir,
)
from slice_index import build_adjust_pairs
from structure_catalog import (
    CCF_ADVANCED_HELP,
    FULL_DETAIL_TIER,
    _structure_map_entry,
    compact_ccf_level_label_and_tooltip,
    format_ccf_level_label,
    get_region,
    list_ccf_levels,
    list_regions_at_level,
    list_regions_for_tier,
    list_tiers,
    load_catalog,
    resolve_label_color,
)
from annotation_exclusion import expand_excluded_ids, apply_exclusion
from apply_parcellation import (
    apply_parcellation_to_slice,
    restore_slice_from_backup,
)
from annotation_relabel import (
    clear_slice_parcellation,
    ensure_full_backup,
    format_applied_parcellation,
    get_slice_parcellation,
    has_full_backup,
    load_full_backup,
    parcellation_target_label,
    relabel_to_target,
    set_slice_parcellation,
)
from qt_image_utils import numpy_array_to_qimage
from qt_window_utils import raise_and_activate


_viewer_exit_reason = "done"


def set_viewer_exit_reason(reason: str) -> None:
    global _viewer_exit_reason
    _viewer_exit_reason = reason


def get_viewer_exit_reason() -> str:
    return _viewer_exit_reason


def qimage_to_numpy_array(qimage):
    """Convert a QImage to a numpy array."""
    # Convert QImage to format RGB32
    qimage = qimage.convertToFormat(QImage.Format.Format_RGB32)

    width = qimage.width()
    height = qimage.height()

    # Get pointer to the data
    ptr = qimage.bits()

    # Interpret the data as a 32-bit integer array
    ptr.setsize(height * width * 4)  # 4 bytes per pixel
    arr = np.array(ptr).reshape((height, width, 4))  # Channels are RGBA

    return arr


class FileSelector(QMainWindow):
    """
    A list of the loaded files with a search bar and buttons to select files
    """

    def __init__(self, files):
        super().__init__()
        self.files = files
        self.selected_file = None
        self.selected_file_index = None
        self.initUI()

    def initUI(self):
        self.setWindowTitle("Select a file")
        self.selected_file = None
        self.selected_file_index = None
        self.search_bar = QLineEdit(self)
        self.search_bar.textChanged.connect(self.search)
        self.file_list = QListWidget(self)
        self.file_list.addItems(self.files)
        self.file_list.itemClicked.connect(self.file_selected)
        self.file_list.itemDoubleClicked.connect(self.file_selected)
        self.file_list.setSortingEnabled(True)

        self.setCentralWidget(self.file_list)

    def search(self):
        search_text = self.search_bar.text()
        if search_text == "":
            self.file_list.clear()
            self.file_list.addItems(self.files)
        else:
            self.file_list.clear()
            self.file_list.addItems([f for f in self.files if search_text in f])

    def file_selected(self, item):
        self.selected_file = item.text()
        self.selected_file_index = self.file_list.index(item)
        self.close()


class _OptionsSectionsContainer(QWidget):
    """Vertical, drag-reorderable stack of the Options sidebar's top-level
    group sections (2026-09-19 user request, 2nd attempt). Ported from
    py/map.py's ReorderableSidebarSections/DraggableSidebarSectionHeader,
    which the user confirmed already works reliably in the Atlas Alignment
    sidebar -- unlike the first attempt here (a QListWidget with each
    section as a setItemWidget() row), which the user reported as
    "bouncing"/glitching when reordering.

    Unlike map.py's version (which builds header+content from raw widget
    lists), each section here is already a complete, self-contained
    QGroupBox with its own _DraggableGroupHeader inserted at layout index 0
    by _make_group_collapsible() -- so this container only needs to track
    section widgets by a stable key and reorder them via real Qt
    QDrag/QMimeData drag-and-drop on its own dragEnterEvent/dragMoveEvent/
    dropEvent, exactly as map.py's container does."""

    MIME_TYPE = "application/x-masonjar-options-sidebar-section"

    def __init__(self, viewer, parent=None):
        super().__init__(parent)
        self._viewer = viewer
        self._sections = {}
        self.setAcceptDrops(True)
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(4)
        # Absorb any leftover vertical space in the Options scroll area
        # here instead of letting Qt spread it across the section widgets
        # themselves (2026-09-19 user report: sections stretched taller
        # than their actual content, both collapsed and expanded). Without
        # a trailing stretch, QVBoxLayout distributes extra space to its
        # children even at QSizePolicy.Preferred -- Preferred still allows
        # growth when nothing else claims the space -- which is exactly
        # what a tall, mostly-empty Options dock was doing to these five
        # sections.
        self._layout.addStretch(1)

    def add_section(self, key: str, widget: QWidget):
        self._sections[str(key)] = widget
        # Vertical Fixed, not whatever the QGroupBox's own default policy
        # is -- belt-and-suspenders alongside __init__'s trailing stretch
        # (which already claims all leftover space on its own): this
        # section can never grow past its actual content height either
        # way. Qt re-queries sizeHint() (and so this Fixed size) whenever
        # the layout is invalidated, which collapsing/expanding already
        # triggers via _sync_options_list_heights()'s updateGeometry().
        policy = widget.sizePolicy()
        policy.setVerticalPolicy(QSizePolicy.Policy.Fixed)
        widget.setSizePolicy(policy)
        # Insert before the trailing stretch (always the layout's last
        # item, see __init__) rather than appending, so new sections keep
        # landing above the absorbed leftover space instead of after it.
        self._layout.insertWidget(self._layout.count() - 1, widget)

    def order(self):
        result = []
        for i in range(self._layout.count()):
            item = self._layout.itemAt(i)
            widget = item.widget() if item is not None else None
            for key, candidate in self._sections.items():
                if candidate is widget:
                    result.append(key)
                    break
        return result

    def dragEnterEvent(self, event):
        if event.mimeData().hasFormat(self.MIME_TYPE):
            event.acceptProposedAction()

    def dragMoveEvent(self, event):
        if event.mimeData().hasFormat(self.MIME_TYPE):
            event.acceptProposedAction()

    def dropEvent(self, event):
        if not event.mimeData().hasFormat(self.MIME_TYPE):
            event.ignore()
            return
        key = bytes(event.mimeData().data(self.MIME_TYPE)).decode("utf-8")
        dragged = self._sections.get(key)
        if dragged is None:
            event.ignore()
            return
        point = event.position().toPoint() if hasattr(event, "position") else event.pos()
        others = [k for k in self.order() if k != key]
        insert_at = len(others)
        for index, other_key in enumerate(others):
            candidate = self._sections[other_key]
            if point.y() < candidate.geometry().center().y():
                insert_at = index
                break
        self._layout.removeWidget(dragged)
        self._layout.insertWidget(insert_at, dragged)
        event.acceptProposedAction()
        if self._viewer is not None:
            self._viewer._save_options_section_order(self)


class _DraggableGroupHeader(QToolButton):
    """A collapsible section header that can also be dragged to reorder its
    section within the Options sidebar's _OptionsSectionsContainer
    (2026-09-19 user request, 2nd attempt -- ported from py/map.py's
    DraggableSidebarSectionHeader, which the user confirmed already works
    reliably elsewhere in the app). Starts a real Qt QDrag carrying this
    section's stable key as QMimeData, rather than the first attempt's
    manual "move the row when the cursor crosses a neighboring midpoint"
    tracking, which the user reported as bouncing/glitching.

    A short move is still a plain click (toggles collapse, via the
    QToolButton base class); only a move past
    QApplication.startDragDistance() starts a drag, and starting one
    suppresses the click-to-toggle that would otherwise fire on release."""

    def __init__(self, *args, viewer=None, **kwargs):
        super().__init__(*args, **kwargs)
        self._viewer = viewer
        self._drag_start_pos = None
        self._dragging = False
        self.setCursor(Qt.CursorShape.OpenHandCursor)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_start_pos = event.pos()
            self._dragging = False
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._drag_start_pos is not None and (event.buttons() & Qt.MouseButton.LeftButton):
            moved = (event.pos() - self._drag_start_pos).manhattanLength()
            if moved >= QApplication.startDragDistance():
                self._drag_start_pos = None
                self._dragging = True
                # Clear the button's own pressed-down visual state before
                # handing control to QDrag's blocking local event loop --
                # otherwise, since the mouse is released somewhere outside
                # this widget once the drag ends, QToolButton never gets
                # its own mouseReleaseEvent to clear that state itself.
                self.setDown(False)
                group = self.parent()
                key = group.property("sectionKey") if group is not None else None
                container = getattr(self._viewer, "_options_sections_container", None)
                if container is not None and key:
                    drag = QDrag(self)
                    mime = QMimeData()
                    mime.setData(
                        _OptionsSectionsContainer.MIME_TYPE,
                        str(key).encode("utf-8"),
                    )
                    drag.setMimeData(mime)
                    self.setCursor(Qt.CursorShape.ClosedHandCursor)
                    drag.exec(Qt.DropAction.MoveAction)
                    self.setCursor(Qt.CursorShape.OpenHandCursor)
                    self.setDown(False)
                self._dragging = False
                return
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        was_dragging = self._dragging
        self._dragging = False
        self._drag_start_pos = None
        if was_dragging:
            # A QDrag already ran to completion; releasing must not also
            # register as a click that toggles collapse.
            return
        super().mouseReleaseEvent(event)


class _SignedSpinBox(QSpinBox):
    """QSpinBox that always displays an explicit sign (+1, -1, ...).

    Used for Compare Adjacent's slice offset (2026-09-22 user request): a
    single signed-number control replaces the earlier Previous/Next
    dropdown paired with a separate steps-away spinbox."""

    def textFromValue(self, value: int) -> str:
        return f"{value:+d}"

    def valueFromText(self, text: str) -> int:
        text = text.strip()
        if not text or text in ("+", "-"):
            return 0
        return int(text)


class AnnotationViewer(QMainWindow):
    def __init__(
        self,
        pairs,
        structure_map,
        images_dir=None,
        previews_dir=None,
        catalog=None,
    ):
        super().__init__()

        self.pairs = pairs
        self.structure_map = structure_map
        self.catalog = catalog
        self._area_combo_updating = False
        self.ccf_advanced = False
        self.current_tier_id = "areas"
        self.images_dir = (
            Path(images_dir) if images_dir else Path(pairs[0][0]).parent
        )
        self.previews_dir = (
            Path(previews_dir)
            if previews_dir
            else resolve_previews_dir(self.images_dir)
        )
        with perf_log.perf_section("adjust.channel.index_previews"):
            self._preview_channel_index = build_lowres_channel_index(
                self.images_dir,
                [slice_id for _, _, slice_id in self.pairs],
                self.previews_dir,
            )
        self.active_channel_name = "DAPI"
        self.active_channel_path = None
        self.active_channel_display_path = None
        self.channel_sources: list[tuple[str, Path]] = []
        self.current_index = 0
        self.current_delta = 0
        self.deltas = []
        self.originals = []
        self._stroke_seen = None
        self.was_changed = False
        self.brush_size = 35
        self.overlay_visible = False
        self.opacity = 100
        self.zoom_level = 100
        self.selected_region_id = None
        self.selected_region_name = "None"
        self._overlay_ready = False
        self._img_pixmap_item = None
        self._img_overlay_item = None
        self._anno_pixmap_item = None
        # Compare Adjacent's own graphics item (2026-09-23), layered
        # above _anno_pixmap_item -- see _set_compare_pixmap().
        self._compare_pixmap_item = None
        self._syncing_scroll = False
        self._pan_scene_initialized = False
        # Set for exactly one show_image_with_overlay() call by
        # _load_section_at() when the newly loaded slice's image pixel
        # size differs from the previously displayed slice's (2026-09-23
        # user request: Next/Previous/Go to should recentre on the new
        # image whenever its size differs, instead of carrying over the
        # old slice's scrollbar pixel values, which show_image_with_
        # overlay() otherwise always preserves verbatim -- correct for a
        # same-size re-render (LUT/Seam/Refresh/Undo), but a stale,
        # differently-scaled position for a genuine size change).
        self._recenter_next_render = False
        # Cached virtual-pan margins per pane (2026-09-19 user report: the slice still moved up/down on LUT slider drags and Seam Correction toggles even after the setSceneRect no-op guard). _lock_scene_rect_to_pixmap() previously recomputed these from view.viewport().size() on every single call; if the toolbar/sidebar's own layout is mid-pass (a label's text changed elsewhere in the same window, a style repolish, etc.) at the exact moment a LUT tick or Seam toggle re-renders, viewport().size() can transiently report a value a pixel or two off from its settled size, which the previous fix's rect-equality guard would treat as a genuine resize and let through. Caching removes viewport() from the hot render path entirely -- these only change on an explicit resize (splitter drag, zoom change), not on every render.
        self._pan_margin_img = (64.0, 64.0)
        self._pan_margin_anno = (64.0, 64.0)
        self._is_panning = False
        self._pan_last_pos = None
        self._space_pan_active = False
        self._right_pan_pending = False
        self._right_pan_start_pos = None
        self._space_down = False
        # Seam correction remains live-only. DAPI results can be reused within
        # this Viewer session and prepared for an adjacent section at idle.
        self._dapi_live_cache: dict[tuple[str, str], Path] = {}
        self._seam_context_cache: dict[tuple[str, str], tuple] = {}
        self._dapi_prefetch_queue: list[tuple[str, Path, str]] = []
        self._dapi_prefetch_generation = 0
        self._dapi_prefetch_future = None
        self._dapi_prefetch_key = None
        self._dapi_prefetch_executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="PFAJarDapiSeam"
        )
        self._dapi_live_cache_tempdir = tempfile.TemporaryDirectory(
            prefix="masonjar-adjust-live-"
        )
        self._save_exit_flag = self.images_dir / ".adjust_save_exit"
        self._save_exit_timer = QTimer(self)
        self._save_exit_timer.timeout.connect(self._poll_save_exit)
        # Debounced (not one-shot-per-event) triggers for the Annotation map
        # toggle / image splitter's virtual-pan refresh (2026-09-19 user
        # report: rapid repeated clicking ("연타") on Annotation map still
        # drifted the slice's position even after _refresh_virtual_pan_scene_rects()
        # itself was fixed to preserve an unchanged view's exact scrollbar
        # values). Root cause: each toggle queued its own fresh
        # QTimer.singleShot(0, ...), so a rapid burst of clicks queued
        # several of these back-to-back; between the moment one of them
        # captures its "previous size/scrollbar" snapshot and the moment it
        # actually applies centerOn(), a *later* queued call could already
        # be mid-flight against the same views, so the "previous" state one
        # callback restored was no longer actually the position the user's
        # last click should have landed on. A restartable (debounced) timer
        # collapses a whole rapid burst into exactly one refresh, run only
        # after clicking actually stops, so there is only ever one
        # snapshot-then-restore in flight at a time.
        self._splitter_refresh_timer = QTimer(self)
        self._splitter_refresh_timer.setSingleShot(True)
        self._splitter_refresh_timer.timeout.connect(
            self._refresh_virtual_pan_scene_rects
        )
        # 2026-09-23 user report (Region picker only, discovered right after
        # the resize hook above was added for the pan-margin bug): on the
        # very first slice, the Region picker's border was drawn too small
        # and its bottom-most content (the resolution warning label, when
        # visible) was clipped -- until Next reloaded the section, which
        # fixed it. Same root cause as the pan-margin bug this timer already
        # exists for: show_maximized_with_default_options_width()'s deferred
        # resizeDocks() calls haven't settled the Options dock's final width
        # yet when the section is first populated, so
        # _update_paint_resolution_warning()'s _resize_paint_resolution_
        # warning() call computes the warning label's setFixedHeight() (and
        # so the whole Fixed-policy section's effective height/border) from
        # a too-narrow pre-settle width. Nothing re-ran that computation
        # once the dock actually reached its final width -- only the pan
        # margins were wired to this debounced resize timer. Reusing it here
        # closes that gap the same way, instead of adding a second, separate
        # timer for what is really the same underlying race.
        self._splitter_refresh_timer.timeout.connect(
            self._update_paint_resolution_warning
        )
        self._splitter_reset_timer = QTimer(self)
        self._splitter_reset_timer.setSingleShot(True)
        self._splitter_reset_timer.timeout.connect(self._reset_image_splitter_equal)
        # 2026-09-19 follow-up: the debounce above collapsed a rapid click
        # burst down to one *callback*, but each click still synchronously
        # flips anno_view.setVisible() right away (that part was never
        # debounced), so a burst of N clicks still causes N real Qt resize
        # events on img_view's viewport before the debounced correction
        # ever runs once at the end. Whatever native anchor behavior Qt
        # applies on each of those N raw resizes (typically re-centering on
        # the viewport's *new* center, not preserving the *original* scene
        # point) compounds across all N of them, and by the time
        # _refresh_virtual_pan_scene_rects() finally fires it can only see
        # the already-compounded result of the last resize -- there is no
        # way to tell, from a live look at img_view alone, how much drift
        # already accumulated from the N-1 earlier ones. Fix: capture the
        # scene point that was actually centered *before the first click in
        # a burst starts moving anything*, hold it here across the whole
        # burst (the guard in _on_annotation_map_toggled only captures once
        # per burst), and have _refresh_virtual_pan_scene_rects() re-target
        # that original point instead of trusting whatever it can see live
        # once the burst settles.
        self._pending_pan_anchor: QPointF | None = None
        try:
            if self._save_exit_flag.is_file():
                self._save_exit_flag.unlink()
        except OSError:
            pass
        self._save_exit_timer.start(200)

        self.annotation_dir = Path(pairs[0][1]).parent
        self.parcel_ccf_advanced = False
        self.parcel_tier_id = "areas"
        self.parcel_preview = False
        self.parcel_preview_array = None
        self.parcel_excluded_ids: list[int] = []

        self.current_label = None
        with open(self.pairs[self.current_index][1], "rb") as f:
            self.current_label = pickle.load(f)

        _, _, _slice_id = self.pairs[self.current_index]
        ensure_full_backup(self.annotation_dir, _slice_id, self.current_label)

        # GUI Components
        self.initUI()

    def initUI(self):
        self.section_info_label = QLabel("", self)
        self.section_info_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        # --- Paint region controls (wired into paint dock below) ---
        self.area_search_box = QLineEdit(self)
        self.area_search_box.setPlaceholderText("Acronym or region name")
        self.area_search_box.setToolTip("Search atlas regions by acronym or name")
        search_completer = QCompleter(self)
        search_completer.setCompletionMode(QCompleter.CompletionMode.PopupCompletion)
        search_completer.setFilterMode(Qt.MatchFlag.MatchContains)
        search_completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.area_search_box.setCompleter(search_completer)
        self.area_search_box.textEdited.connect(self._on_area_search_box_edited)
        self.area_search_box.returnPressed.connect(
            lambda: self._commit_search_text(self.area_search_box.text())
        )
        search_completer.activated.connect(self._on_area_search_completer_activated)

        self.tier_combo = QComboBox(self)
        self.tier_combo.setToolTip("Semantic hierarchy tier for region picker")
        self.tier_combo.currentIndexChanged.connect(self._on_tier_changed)
        self.level_combo = QComboBox(self)
        self.level_combo.setToolTip("CCFv3 structure level (advanced mode)")
        self.level_combo.currentIndexChanged.connect(self._on_level_changed)
        self.level_combo.setEnabled(False)
        self.area_combo = QComboBox(self)
        self.area_combo.setEditable(True)
        self.area_combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.area_combo.setMinimumWidth(80)
        self.area_combo.setToolTip("Atlas region to paint with the brush")
        area_completer = self.area_combo.completer()
        area_completer.setCompletionMode(QCompleter.CompletionMode.PopupCompletion)
        area_completer.setFilterMode(Qt.MatchFlag.MatchContains)
        area_completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.area_combo.lineEdit().textChanged.connect(self._on_area_search_changed)
        self.area_combo.activated.connect(self._on_area_activated)
        self.ccf_advanced_toggle = QCheckBox("CCFv3 depths", self)
        self.ccf_advanced_toggle.setChecked(False)
        self.ccf_advanced_toggle.setToolTip(CCF_ADVANCED_HELP)
        self.ccf_advanced_toggle.toggled.connect(self._on_ccf_advanced_toggled)
        self._tier_change_notice_shown = False

        # --- Paint target strip (must exist before _init_paint_region_controls) ---
        self.paint_swatch = QLabel(self)
        self.paint_swatch.setFixedSize(18, 18)
        self.paint_swatch.setFrameShape(QFrame.Shape.Box)
        self.paint_target_name = QLabel("None", self)
        # Full anatomical name, shown on its own row below the swatch/acronym
        # line (2026-09-18 user request) -- previously the full name was only
        # reachable by hovering paint_target_name's tooltip.
        self.paint_target_fullname = QLabel("", self)
        self.paint_target_fullname.setWordWrap(True)
        self.paint_target_fullname.setStyleSheet("color: #ffffff;")
        self.paint_tier_context = QLabel("", self)
        self.paint_adjust_badge = QLabel("OFF", self)

        self.opacity_slider = QSlider(Qt.Orientation.Horizontal)
        self.opacity_slider.setRange(0, 255)
        self.opacity_slider.setValue(self.opacity)
        self.opacity_slider.valueChanged.connect(self.update_opacity)
        self.opacity_label = QLabel("Opacity", self)

        self.zoom_label = QLabel(f"Zoom {self.zoom_level}%", self)
        self.zoom_slider = QSlider(Qt.Orientation.Horizontal)
        self.zoom_slider.setRange(50, 1000)
        self.zoom_slider.setValue(self.zoom_level)
        self.zoom_slider.valueChanged.connect(self.update_zoom)

        self.brush_label = QLabel(f"Brush {self.brush_size}", self)
        self.brush_slider = QSlider(Qt.Orientation.Horizontal)
        self.brush_slider.setRange(1, 50)
        self.brush_slider.setValue(self.brush_size)
        self.brush_slider.valueChanged.connect(self.update_brush)

        # Brightness/contrast LUT for the background channel image
        # (2026-09-18 user request). Applied on top of the already
        # percentile-stretched 8-bit preview PNG -- a palette-style remap,
        # not a recovery of clipped dynamic range. lut_black/lut_white are
        # 0-255 input cutoffs; lut_gamma is gamma*100 (100 == 1.00, linear)
        # so the slider can stay an int QSlider like the others.
        self.lut_black = 0
        self.lut_white = 255
        self.lut_gamma = 100
        self._active_channel_array = None

        self.lut_black_label = QLabel("Black 0", self)
        self.lut_black_slider = QSlider(Qt.Orientation.Horizontal)
        self.lut_black_slider.setRange(0, 254)
        self.lut_black_slider.setValue(self.lut_black)
        self.lut_black_slider.valueChanged.connect(self._on_lut_black_changed)

        self.lut_white_label = QLabel("White 255", self)
        self.lut_white_slider = QSlider(Qt.Orientation.Horizontal)
        self.lut_white_slider.setRange(1, 255)
        self.lut_white_slider.setValue(self.lut_white)
        self.lut_white_slider.valueChanged.connect(self._on_lut_white_changed)

        self.lut_gamma_label = QLabel("Gamma 1.00", self)
        self.lut_gamma_slider = QSlider(Qt.Orientation.Horizontal)
        self.lut_gamma_slider.setRange(10, 300)
        self.lut_gamma_slider.setValue(self.lut_gamma)
        self.lut_gamma_slider.valueChanged.connect(self._on_lut_gamma_changed)

        self.lut_reset_button = QPushButton("Reset", self)
        self.lut_reset_button.setToolTip("Reset black/white/gamma to identity (0 / 255 / 1.00)")
        self.lut_reset_button.clicked.connect(self._reset_lut)

        self.lut_auto_button = QPushButton("Auto", self)
        self.lut_auto_button.setToolTip(
            "Set black/white from the 1st/99th percentile of this channel's "
            "tissue pixels (background excluded), and gamma from where the "
            "tissue's median brightness falls in that range."
        )
        self.lut_auto_button.clicked.connect(self._auto_lut)

        # A shared label column keeps all sliders aligned even as
        # Zoom/Brush text changes with their current values.
        for slider_label in (
            self.opacity_label,
            self.zoom_label,
            self.brush_label,
            self.lut_black_label,
            self.lut_white_label,
            self.lut_gamma_label,
        ):
            slider_label.setFixedWidth(88)

        self.refresh_button = QPushButton("Refresh", self)
        self.refresh_button.setToolTip(
            "Redraw annotation overlay from current edits "
            "(does not change region IDs)."
        )
        self.refresh_button.clicked.connect(self.refresh_drawings)
        self.undo_button = QPushButton("Undo", self)
        self.undo_button.clicked.connect(self.undo_last_delta)
        self.save_button = QPushButton("Save", self)
        self.save_button.clicked.connect(self.save_changes)

        # --- Image views (central widget) ---
        self.img_view = QGraphicsView(self)
        self.anno_view = QGraphicsView(self)
        self.anno_scene = QGraphicsScene(self)
        self.anno_view.setScene(self.anno_scene)
        self.img_scene = QGraphicsScene(self)
        self.img_pixmap = QPixmap()
        self.img_view.setScene(self.img_scene)
        self._configure_dual_views()

        self._brush_cursor_img = QGraphicsEllipseItem()
        self._brush_cursor_img.setZValue(1000)
        self._brush_cursor_img.setVisible(False)
        self.img_scene.addItem(self._brush_cursor_img)
        self._brush_cursor_anno = QGraphicsEllipseItem()
        self._brush_cursor_anno.setZValue(1000)
        self._brush_cursor_anno.setVisible(False)
        self.anno_scene.addItem(self._brush_cursor_anno)
        # Brush ring cursor appearance (configurable in the Brush panel).
        # Default to a high-contrast color for visibility over any background.
        self.brush_cursor_color = QColor("#FFFF00")
        self.brush_cursor_width = 3
        self.brush_cursor_use_region = False

        # Default: annotation map on the left, DAPI image on the right.
        # _views_swapped=True mirrors the "anno-left" branch of _swap_views().
        self.image_splitter = QSplitter(Qt.Orientation.Horizontal, self)
        self.image_splitter.setChildrenCollapsible(False)
        self.image_splitter.addWidget(self.anno_view)
        self.image_splitter.addWidget(self.img_view)
        self.image_splitter.splitterMoved.connect(self._on_image_splitter_moved)
        self._views_swapped = True
        self.setCentralWidget(self.image_splitter)
        QTimer.singleShot(0, self._reset_image_splitter_equal)

        self.is_drawing = False
        self.last_draw_point = None
        # Compare Adjacent (2026-09-22 user request): temporarily shows an
        # adjacent slice's DAPI in the Annotation pane for visual
        # comparison, read-only. See _enter_compare_mode()/_exit_compare_mode().
        self._compare_mode_active = False
        # Cached label array for the slice currently shown in the
        # comparison pane, so a right-click there can pick from *that*
        # slice's annotation instead of the current slice's (2026-09-22
        # user request). None whenever compare mode is off or the
        # adjacent slice has no loadable annotation.
        self._compare_adjacent_label_array = None
        # Where _enter_compare_mode() actually drew the adjacent picture
        # inside its (possibly padded/centered) compare canvas, and that
        # picture's own native (pre-padding) size -- both needed by
        # _select_paint_target_at_view_pos() to map a click back into
        # _compare_adjacent_label_array's coordinates and to reject a
        # click that landed on the black padding margin instead of the
        # actual picture (2026-09-23 user report: a right-click that
        # visibly hit nothing was still silently resolving to *some*
        # region, because that coordinate math had not been updated when
        # the picture stopped being scaled to fill the whole canvas).
        self._compare_pixmap_offset = (0, 0)
        self._compare_pixmap_native_size = (0, 0)
        # Whether _enter_compare_mode() forced the Annotation map pane
        # visible because it was hidden (2026-09-22 user request); if so,
        # _exit_compare_mode() hides it again on the way out.
        self._compare_prev_annotation_map_checked = None
        # Slice id that _compare_adjacent_label_array belongs to, for a
        # confirmation message on right-click pick (2026-09-23).
        self._compare_adjacent_slice_id = None
        # Region picker: which of Search/Area currently has a
        # click-triggered select-all pending (2026-09-22, see eventFilter's
        # FocusIn/MouseButtonPress handling for area_search_box/
        # area_combo.lineEdit() below).
        self._region_picker_pending_select_all = set()
        # Cached overlay RGBA for incremental stroke updates (avoids a full-frame
        # rebuild on every brush release). _stroke_bbox tracks the changed region.
        self._anno_rgba = None
        self._stroke_bbox = None
        # Active brush strokes use a compact boolean visit map plus NumPy Undo
        # chunks. Existing non-brush actions retain their set/dict Undo form.
        self._stroke_seen = None
        self.img_view.viewport().setAttribute(
            Qt.WidgetAttribute.WA_AcceptTouchEvents, False
        )
        self.anno_view.viewport().setAttribute(
            Qt.WidgetAttribute.WA_AcceptTouchEvents, False
        )
        self.img_view.viewport().setContextMenuPolicy(
            Qt.ContextMenuPolicy.NoContextMenu
        )
        self.anno_view.viewport().setContextMenuPolicy(
            Qt.ContextMenuPolicy.NoContextMenu
        )

        # --- Options dock (Paint + Parcellation in one single-scroll panel) ---
        self.paint_dock = QDockWidget("Options", self)
        self.paint_dock.setObjectName("OptionsDock")
        self.paint_dock.setFeatures(
            QDockWidget.DockWidgetFeature.DockWidgetMovable
            | QDockWidget.DockWidgetFeature.DockWidgetFloatable
            | QDockWidget.DockWidgetFeature.DockWidgetClosable
        )
        self._options_settings = QSettings("PFAJar", "PFAJar")
        options_inner = QWidget(self)
        options_layout = QVBoxLayout()
        options_layout.setContentsMargins(4, 4, 4, 4)
        self._init_paint_controls(options_layout)
        self._init_parcellation_controls(options_layout)
        # Replace the plain top-to-bottom stack with a drag-reorderable
        # _OptionsSectionsContainer (2026-09-19 user request: let the
        # Options sidebar's main sections be rearranged by dragging, and
        # remember the arrangement; ported from py/map.py's proven
        # ReorderableSidebarSections after a first QListWidget-based
        # attempt bounced/glitched). Only reparents the group boxes
        # already built above; every self.xxx widget reference is
        # unaffected.
        options_layout.addWidget(self._wrap_options_sections_reorderable(options_layout))
        options_inner.setLayout(options_layout)
        self._options_inner = options_inner
        # Keep the full option stack as the scroll area's content size. Without
        # an explicit minimum height, a floating/narrow dock can compress the
        # child widget and Qt incorrectly concludes that no vertical overflow
        # exists, leaving the Display section clipped with no scrollbar.
        # _sync_options_list_heights() nudges the reorderable container's
        # own sizeHint (which now tracks its visible children automatically)
        # through to this scroll area on every collapse/expand, rather than
        # the one-time options_layout.sizeHint() this replaced.
        self._sync_options_list_heights()
        options_scroll = QScrollArea(self)
        options_scroll.setWidgetResizable(True)
        options_scroll.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded
        )
        options_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        options_scroll.setWidget(options_inner)
        self.paint_dock.setWidget(options_scroll)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.paint_dock)
        stored_width = self._options_settings.value(
            "adjustment/optionsDockWidth", 0
        )
        try:
            self._saved_options_dock_width = max(0, int(stored_width))
        except (TypeError, ValueError):
            self._saved_options_dock_width = 0
        self._options_width_ready = False
        self.paint_dock.installEventFilter(self)

        # --- Header toolbar (navigation + view essentials only) ---
        header_toolbar = QToolBar("Header", self)
        header_toolbar.setMovable(False)
        self.addToolBar(Qt.ToolBarArea.TopToolBarArea, header_toolbar)
        header_toolbar.addWidget(self.section_info_label)
        header_toolbar.addSeparator()

        self.channel_combo = QComboBox(self)
        self.channel_combo.setMinimumWidth(180)
        self.channel_combo.currentIndexChanged.connect(self._on_channel_combo_changed)
        header_toolbar.addWidget(QLabel("Channel:", self))
        header_toolbar.addWidget(self.channel_combo)
        # Toggle between the plain DAPI/channel image and its seam-corrected
        # counterpart (2026-09-06, S1<-S2 handoff completed: the seam batch
        # output location was finalized in js/preprocess_wizard.js's
        # buildOutputPath()/pipeline_runs.js's RUN_STEP_CONFIG.seam as
        # 03_max/seam/<slug>/, not the old 00_dapi_basic sibling convention
        # BaSiC used -- see _seam_root()/_seam_sibling_path() below).
        # Hybrid behavior (user request, 2026-09-05/06): a precomputed seam
        # batch output for this slice is shown instantly when one exists;
        # otherwise the user is asked whether to compute it live for this
        # slice only (seam_correct.correct() is pure numpy/cv2 -- no
        # subprocess needed, this viewer already runs in the same masonjar
        # Python env as seam_correct.py). See
        # _on_seam_channel_toggled()/_compute_seam_live().
        self.seam_channel_toggle = QPushButton("Seam\ncorrection", self)
        self.seam_channel_toggle.setCheckable(True)
        self.seam_channel_toggle.setToolTip(
            "Show a live seam-corrected DAPI/channel image."
        )
        self.seam_channel_toggle.setEnabled(False)
        self.seam_channel_toggle.toggled.connect(self._on_seam_channel_toggled)
        # The separate Known-geometry/Grid-estimated line under this button
        # (a second QLabel row, with a matching blank spacer reserved above
        # the button so every other toolbar button wasn't pushed off-centre
        # by it) is gone (2026-09-19 user request): the mode is now folded
        # into this button's own tooltip by _update_seam_mode_label()
        # instead. That both lets this button be a normal single-row
        # control like its neighbours and removes the whole
        # seam_control/seam_layout wrapper widget that existed only to
        # stack the button over that label.
        header_toolbar.addWidget(self.seam_channel_toggle)
        self.annotation_map_toggle = QPushButton("Annotation\nmap", self)
        self.annotation_map_toggle.setCheckable(True)
        self.annotation_map_toggle.setChecked(True)
        self.annotation_map_toggle.setToolTip(
            "Show or hide the annotation map. When hidden, the DAPI view uses "
            "the available central workspace."
        )
        self.annotation_map_toggle.toggled.connect(self._on_annotation_map_toggled)
        header_toolbar.addWidget(self.annotation_map_toggle)

        self.swap_views_button = QPushButton("Swap\nMap/DAPI", self)
        self.swap_views_button.setToolTip(
            "Swap the left/right positions of the DAPI image and annotation map."
        )
        self.swap_views_button.clicked.connect(self._swap_views)
        header_toolbar.addWidget(self.swap_views_button)
        header_toolbar.addSeparator()

        # Toggle Overlay: briefly moved beside the "Brush" section's title
        # (2026-09-19), reverted the same day after user feedback that it
        # broke that title's left alignment with the other sections -- back
        # in its original spot here.
        self.overlay_toggle = QPushButton("Toggle\nOverlay", self)
        self.overlay_toggle.setCheckable(True)
        self.overlay_toggle.setChecked(self.overlay_visible)
        self.overlay_toggle.setToolTip(
            "Show or hide the colored annotation overlay on the DAPI image. "
            "Shortcut: Tab."
        )
        self.overlay_toggle.toggled.connect(self.toggle_overlay)
        header_toolbar.addWidget(self.overlay_toggle)

        self.allow_adjustment = QPushButton("Allow\nAdjustment", self)
        self.allow_adjustment.setCheckable(True)
        self.allow_adjustment.setChecked(False)
        self.allow_adjustment.toggled.connect(
            lambda _checked: self._update_paint_target_strip()
        )
        header_toolbar.addWidget(self.allow_adjustment)

        # Compare Adjacent (2026-09-22 user request): lets the user see the
        # previous/next slice's DAPI without leaving the current slice, by
        # temporarily swapping the Annotation pane's display for it.
        # Read-only -- see _enter_compare_mode().
        self.compare_adjacent_toggle = QPushButton("Compare\nAdjacent", self)
        self.compare_adjacent_toggle.setCheckable(True)
        self.compare_adjacent_toggle.setChecked(False)
        self.compare_adjacent_toggle.setToolTip(
            "Temporarily show a nearby slice's DAPI in the Annotation pane "
            "for visual comparison. The comparison pane itself is "
            "read-only, but the current slice stays editable as normal "
            "(e.g. via the DAPI pane). Toggle off to return to the "
            "current slice's annotation map."
        )
        self.compare_adjacent_toggle.toggled.connect(
            self._on_compare_adjacent_toggled
        )

        # Offset (2026-09-22 user request, revised same day): a single
        # signed number replaces the earlier Previous/Next dropdown paired
        # with a separate steps-away spinbox -- e.g. -2 compares two
        # sections back from the current one, +1 compares the immediately
        # next one.
        self.compare_offset_spin = _SignedSpinBox(self)
        self.compare_offset_spin.setRange(-999, 999)
        self.compare_offset_spin.setValue(-1)
        self.compare_offset_spin.setToolTip(
            "Which section to compare against, relative to the current "
            "one (e.g. -2 = two sections back, +1 = the next section)."
        )
        self.compare_offset_spin.valueChanged.connect(
            self._on_compare_offset_changed
        )

        # Stacked 2-row/1-column layout (2026-09-22 user request) instead
        # of side-by-side: keeps the Compare Adjacent control's total
        # toolbar width down to a single widget's worth, which is what had
        # been pushing Next/Go to... behind the toolbar's overflow chevron.
        compare_container = QWidget(self)
        compare_layout = QVBoxLayout(compare_container)
        compare_layout.setContentsMargins(0, 0, 0, 0)
        compare_layout.setSpacing(2)
        compare_layout.addWidget(self.compare_adjacent_toggle)
        compare_layout.addWidget(self.compare_offset_spin)
        header_toolbar.addWidget(compare_container)
        header_toolbar.addSeparator()

        self.paint_dock_button = QPushButton("Options", self)
        self.paint_dock_button.setCheckable(True)
        self.paint_dock_button.setChecked(True)
        self.paint_dock_button.clicked.connect(self._toggle_paint_dock)
        self.paint_dock.visibilityChanged.connect(self._on_paint_dock_visibility)

        # Vertically expand the header toolbar's own controls to fill the
        # toolbar's full height, matching Previous/Next/Go to... below
        # (2026-09-19 user request: uniform top/bottom sizing). Channel
        # (the QComboBox) is deliberately excluded -- user follow-up asked
        # to cancel the expand policy there specifically, leaving it at its
        # normal combo-box height.
        for header_widget in (
            self.seam_channel_toggle,
            self.annotation_map_toggle,
            self.swap_views_button,
            self.overlay_toggle,
            self.allow_adjustment,
            self.paint_dock_button,
        ):
            header_widget.setSizePolicy(
                QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding
            )

        # Keep section navigation at the far edge regardless of the Options
        # dock width or the current Seam mode label.
        header_spacer = QWidget(self)
        header_spacer.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred
        )
        header_toolbar.addWidget(header_spacer)
        self.prev_button = QPushButton("Previous", self)
        self.prev_button.clicked.connect(self.prev_image)
        self.next_button = QPushButton("Next", self)
        self.next_button.clicked.connect(self.next_image)
        self.goto_button = QPushButton("Go to…", self)
        self.goto_button.clicked.connect(self.goto_image)
        # Keep navigation controls as tall as the header instead of their
        # text height, matching the Alignment navigation grid.
        for button in (self.prev_button, self.next_button, self.goto_button):
            button.setSizePolicy(
                QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding
            )
        header_toolbar.addWidget(self.prev_button)
        header_toolbar.addWidget(self.next_button)
        header_toolbar.addWidget(self.goto_button)
        # Options toggle moved next to Go to... (2026-09-22 user
        # request), out of the left-hand toggle cluster.
        header_toolbar.addWidget(self.paint_dock_button)
        header_right_margin = QWidget(self)
        header_right_margin.setFixedWidth(20)
        header_toolbar.addWidget(header_right_margin)

        # Status bar
        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)

        self.anno_view.setMouseTracking(True)
        self.anno_view.viewport().installEventFilter(self)
        self.img_view.setMouseTracking(True)
        self.img_view.viewport().installEventFilter(self)
        self.img_view.installEventFilter(self)
        self.anno_view.installEventFilter(self)
        self._install_view_shortcuts()

        self._update_section_labels()
        channel_loaded = self.rebuild_channel_combo()
        self._init_paint_region_controls()
        self._update_paint_target_strip()
        if not channel_loaded:
            self.show_image_with_overlay()
        for editor in self.findChildren(QLineEdit):
            editor.installEventFilter(self)
        self.installEventFilter(self)

    def _swap_views(self):
        """Swap the left/right positions of the DAPI image and annotation views."""
        self._views_swapped = not self._views_swapped
        if self._views_swapped:
            self.image_splitter.insertWidget(0, self.anno_view)
            self.image_splitter.insertWidget(1, self.img_view)
        else:
            self.image_splitter.insertWidget(0, self.img_view)
            self.image_splitter.insertWidget(1, self.anno_view)

    def _on_annotation_map_toggled(self, visible: bool):
        """Hide the map pane without changing its overlay or editing state."""
        if self._pending_pan_anchor is None and self.img_pixmap is not None:
            # Only the *first* click of a rapid burst should set this --
            # see the comment on _pending_pan_anchor's declaration. A later
            # click within the same still-unsettled burst must not
            # overwrite it with an already-drifted intermediate center.
            self._pending_pan_anchor = self._viewport_center_scene_pos(
                self.img_view
            )
        self.anno_view.setVisible(bool(visible))
        if visible:
            # The requested restore policy is 5:5 after the map has been
            # hidden; otherwise the splitter keeps the user's drag position.
            # Debounced (2026-09-19, see _splitter_reset_timer's setup
            # comment) -- rapid repeated toggling restarts this instead of
            # queueing a fresh one-shot per click.
            self._splitter_reset_timer.start(30)
        # QSplitter reallocates a hidden pane's width to DAPI.  Defer repaint
        # until the new viewport geometry is available to Qt.
        QTimer.singleShot(0, self.img_view.viewport().update)

    def _reset_image_splitter_equal(self):
        """Restore visible DAPI and Annotation map panes to a 50:50 split."""
        if not self.anno_view.isVisible():
            return
        width = max(2, self.image_splitter.size().width())
        left = width // 2
        self.image_splitter.setSizes([left, width - left])

    def _on_image_splitter_moved(self, _position: int, _index: int):
        """Keep both viewports on one scene point after a divider drag.

        Debounced (2026-09-19, see _splitter_refresh_timer's setup comment
        in __init__) -- a drag emits many splitterMoved events in quick
        succession (and Annotation map toggling under rapid clicking can
        also queue several setSizes()-driven moves back-to-back), so this
        restarts one shared timer instead of queueing a fresh
        QTimer.singleShot(0, ...) per event; only the last event in a burst
        actually triggers the refresh, once movement has actually stopped.
        """
        self._splitter_refresh_timer.start(30)

    def _toggle_paint_dock(self):
        self.paint_dock.setVisible(self.paint_dock_button.isChecked())

    def _on_paint_dock_visibility(self, visible: bool):
        self.paint_dock_button.blockSignals(True)
        self.paint_dock_button.setChecked(visible)
        self.paint_dock_button.blockSignals(False)

    def show_maximized_with_default_options_width(self):
        """Maximize the viewer and reserve one quarter for the Options dock."""
        self.showMaximized()

        def apply_options_width():
            if not self.paint_dock.isVisible():
                return
            screen = self.screen() or QApplication.primaryScreen()
            if screen is None:
                return
            target_width = self._saved_options_dock_width or round(
                screen.availableGeometry().width() * 0.25
            )
            minimum_width = max(
                self.paint_dock.minimumWidth(),
                self.paint_dock.minimumSizeHint().width(),
            )
            self._options_width_ready = True
            self.resizeDocks(
                [self.paint_dock],
                [max(target_width, minimum_width)],
                Qt.Orientation.Horizontal,
            )

        # The first call catches normal displays; the second runs after Qt has
        # completed the maximize/dock layout pass on Windows.
        QTimer.singleShot(0, apply_options_width)
        QTimer.singleShot(200, apply_options_width)

    def _update_section_labels(self):
        """Primary slice id, section ordinal, and file basenames."""
        if not self.pairs:
            self.section_info_label.setText("")
            self.setWindowTitle("Adjustment Viewer")
            return
        _, anno_path, slice_id = self.pairs[self.current_index]
        n = self.current_index + 1
        m = len(self.pairs)
        anno_base = Path(anno_path).name
        bg = self.active_channel_name or "DAPI"
        bg_file = (
            self.active_channel_display_path.name
            if self.active_channel_display_path
            else ""
        )
        self.section_info_label.setText(
            f"{slice_id}\nSection {n} of {m}\n"
            f"Background: {bg}"
            + (f" ({bg_file})" if bg_file else "")
            + f"\n{anno_base}"
        )
        self.setWindowTitle(f"Adjustment Viewer — {slice_id}")

    def rebuild_channel_combo(self) -> bool:
        """Rebuild background channel combo for the current slice.

        Returns True when a channel image was loaded (switch_channel ran).
        """
        channel_combo = self.__dict__.get("channel_combo")
        if channel_combo is None:
            return False

        self.channel_combo.blockSignals(True)
        self.channel_combo.clear()

        _, _, slice_id = self.pairs[self.current_index]
        self.channel_sources = lowres_channels_for_slice(
            self.images_dir,
            slice_id,
            self.previews_dir,
            self._preview_channel_index,
        )

        if not self.channel_sources:
            self.channel_combo.addItem("No preview channels found", None)
            self.channel_combo.setEnabled(False)
            self.channel_combo.blockSignals(False)
            status_bar = getattr(self, "status_bar", None)
            if status_bar is not None:
                status_bar.showMessage(
                    "No preview channels — add _previews PNGs or 00_dapi PNG for this slice."
                )
            return False

        default_index = 0
        dapi_index = None
        active_index = None
        active_name = getattr(self, "active_channel_name", None)
        self.channel_combo.setEnabled(True)
        for i, (name, path) in enumerate(self.channel_sources):
            self.channel_combo.addItem(name, str(path))
            if name in ("DAPI", "DAPI (pipeline)", "Dapi"):
                dapi_index = i
            if active_name and name == active_name and active_index is None:
                active_index = i

        # Keep whatever background channel (DAPI/Somata/Starters/...) the user
        # was already viewing when navigating to a different section (Prev,
        # Next, Go to). Only fall back to DAPI -- and then the first entry --
        # when the new section has no channel by that same name.
        if active_index is not None:
            default_index = active_index
        elif dapi_index is not None:
            default_index = dapi_index

        self.channel_combo.setCurrentIndex(default_index)
        self.channel_combo.blockSignals(False)
        name, path = self.channel_sources[default_index]
        seam_enabled = bool(
            getattr(self, "seam_channel_toggle", None)
            and self.seam_channel_toggle.isChecked()
        )
        cached_live = (
            self._cached_dapi_live_path(slice_id, path)
            if seam_enabled and self._is_dapi_channel(name)
            else None
        )
        if cached_live is not None:
            # A revisited DAPI section must not briefly load the original PNG
            # before replacing it with its already-prepared live result.
            print(f"LOG: seam_live_cache_hit slice={slice_id}", flush=True)
            self.switch_channel(cached_live, name, display_path=path)
            self._refresh_seam_toggle_state()
            self._schedule_adjacent_dapi_prefetch()
            return True
        self.switch_channel(path, name)
        self._refresh_seam_toggle_state()
        # Moving to a different section (prev/next -> _load_section_at() ->
        # here) keeps the "Seam corrected" toggle's checked state as-is; if
        # it is still checked, silently re-apply seam correction to the new
        # section's default channel -- never prompting here, even the very
        # first time (2026-09-08 user request: no popup on section change).
        if seam_enabled:
            self._apply_seam_correction(path, name, interactive=False)
        return True

    def _seam_root(self) -> Path:
        """`<bundle>/data/counting/03_max/seam` -- where the Seam Correction
        wizard (js/preprocess_wizard.js buildOutputPath(), stepId==="seam")
        writes its batch output for the DAPI branch. Mirrors how
        `_previews`/the old `00_dapi_basic` sibling were derived from
        self.images_dir elsewhere in this file: assumes the default
        canonical role layout (00_dapi and 03_max both directly under
        data/counting/), does not account for a per-project role override."""
        return self.images_dir.parent / "03_max" / "seam"

    def _seam_sibling_path(self, path: Path) -> Path | None:
        """Return the most recently produced seam-corrected sibling of
        *path*, if any exists under _seam_root(). The wizard can write
        multiple batch runs (one per distinct run config) under
        03_max/seam/<slug>/, so this scans every immediate subfolder for a
        file matching this slice's stem and keeps the newest by mtime --
        the same "most recent wins" heuristic js/max_datasets.js's
        defaultDatasetForBranch() already uses for its own per-branch
        dataset picks. _compute_seam_live()'s on-the-fly cache
        (03_max/seam/_live_preview/) is just another such subfolder, so a
        real batch output with a newer mtime naturally takes precedence
        over a stale live-computed cache."""
        seam_root = self._seam_root()
        if not seam_root.is_dir():
            return None
        stem = Path(path).stem
        best: Path | None = None
        best_mtime = -1.0
        try:
            subdirs = [d for d in seam_root.iterdir() if d.is_dir()]
        except OSError:
            return None
        for sub in subdirs:
            for ext in (".png", ".tif", ".tiff", ".jpg", ".jpeg"):
                candidate = sub / f"{stem}{ext}"
                if candidate.is_file():
                    try:
                        mtime = candidate.stat().st_mtime
                    except OSError:
                        continue
                    if mtime > best_mtime:
                        best_mtime = mtime
                        best = candidate
                    break
        return best

    def _live_seam_context_for(self, path: Path, seam_correct, seam_slice_id: str):
        """Return the canonical import metadata for a live preview image.

        The active image is often ``_previews/<slice>_dapi.png``.  Keep its
        pixels as the correction input, while using the matching pipeline
        DAPI image for the seamgrid identity and geometry-history lookup.
        """
        path = Path(path)
        context_key = (str(seam_slice_id), str(path.resolve()).casefold())
        cached = self._seam_context_cache.get(context_key)
        if cached is not None:
            print(f"LOG: seam_live_context_cache_hit slice={seam_slice_id}", flush=True)
            return cached
        meta_dir = None
        seen_roots = set()
        for start in (Path(path).parent, self.images_dir):
            for root in (start,) + tuple(start.parents):
                if root in seen_roots:
                    continue
                seen_roots.add(root)
                candidate = root / ".masonjar"
                if candidate.is_dir():
                    meta_dir = candidate
                    break
            if meta_dir is not None:
                break
        geometry_path = self.images_dir / f"{seam_slice_id}.png"
        if not geometry_path.is_file():
            geometry_path = Path(path)
        sidecar = (
            meta_dir / "seamgrid" / f"{seam_slice_id}.json"
            if meta_dir is not None
            else None
        )
        grid = seam_correct.load_seam_grid(
            meta_dir, seam_slice_id, geometry_path
        )
        context = (meta_dir, seam_slice_id, geometry_path, sidecar, grid)
        self._seam_context_cache[context_key] = context
        return context

    def _live_seam_context(self, path: Path, seam_correct):
        return self._live_seam_context_for(
            path, seam_correct, self._current_slice_id()
        )

    def _compute_seam_live_for(
        self,
        path: Path,
        seam_slice_id: str,
        dest: Path,
        *,
        prefetch: bool = False,
    ) -> Path | None:
        """Compute one live result without touching Qt state.

        The method is safe for the single DAPI prefetch worker because every
        parameter is an immutable path or slice identifier captured on the UI
        thread before the task begins.
        """
        try:
            import seam_correct

            dest.parent.mkdir(parents=True, exist_ok=True)
            # Match Seam Correction's default: valid imported CZI tile
            # geometry wins; unavailable/invalid geometry falls back to the
            # established Grid-estimated mode.
            meta_dir, seam_slice_id, geometry_path, sidecar, grid = (
                self._live_seam_context_for(path, seam_correct, seam_slice_id)
            )
            print(
                "LOG: seam_live_context "
                f"source={path} images_dir={self.images_dir} "
                f"meta_dir={meta_dir} slice_id={seam_slice_id} "
                f"geometry_path={geometry_path} sidecar={sidecar} "
                f"sidecar_exists={bool(sidecar and sidecar.is_file())} "
                f"known_geometry_available={grid is not None}",
                flush=True,
            )
            info = seam_correct.correct_file_to(
                dest,
                path,
                band=seam_correct.DEFAULT_BAND,
                meta_dir=meta_dir,
                seam_mode="auto",
                seamgrid_slice_id=seam_slice_id,
                geometry_image_path=geometry_path,
            )
            print(
                "LOG: seam_live_preview "
                f"slice={seam_slice_id} mode={info.get('mode')} "
                f"band={info.get('band')} n_seams={info.get('n_seams')} "
                f"prefetch={int(prefetch)}",
                flush=True,
            )
            return dest
        except Exception as exc:  # noqa: BLE001
            print(f"LOG: seam_live_preview_failed {exc!r}", flush=True)
            return None

    def _compute_seam_live(self, path: Path) -> Path | None:
        cache_dir = self._seam_root() / "_live_preview"
        # DAPI, nuclei, and somata can share one slice ID.  A slice-only
        # filename lets a later signal-channel correction overwrite the DAPI
        # result that the DAPI session cache points to.
        channel_tag = hashlib.sha256(
            str(Path(path).resolve()).encode("utf-8")
        ).hexdigest()[:12]
        return self._compute_seam_live_for(
            path,
            self._current_slice_id(),
            cache_dir / f"{self._current_slice_id()}_{channel_tag}.png",
        )

    def _compute_seam_live_for_slice(self, path: Path, slice_id: str) -> Path | None:
        """Like _compute_seam_live(), but for an arbitrary *slice_id*
        instead of always the current section -- used by
        _enter_compare_mode() so Compare Adjacent's reference image
        reflects Seam Correction too (2026-09-23 user request), not just
        the current slice's own DAPI/channel pane."""
        cache_dir = self._seam_root() / "_live_preview"
        channel_tag = hashlib.sha256(
            str(Path(path).resolve()).encode("utf-8")
        ).hexdigest()[:12]
        return self._compute_seam_live_for(
            path, slice_id, cache_dir / f"{slice_id}_{channel_tag}.png",
        )

    @staticmethod
    def _is_dapi_channel(name: str) -> bool:
        return str(name or "").strip().casefold().startswith("dapi")

    def _dapi_live_cache_key(self, slice_id: str, path: Path) -> tuple[str, str]:
        return str(slice_id), str(Path(path).resolve()).casefold()

    def _cached_dapi_live_path(self, slice_id: str, path: Path) -> Path | None:
        key = self._dapi_live_cache_key(slice_id, path)
        cached = self._dapi_live_cache.get(key)
        if cached is not None and cached.is_file():
            return cached
        self._dapi_live_cache.pop(key, None)
        return None

    def _harvest_completed_dapi_prefetch(self) -> bool:
        """Store a completed prefetch before a navigation invalidates its queue."""
        future = self._dapi_prefetch_future
        if future is None or not future.done():
            return False
        key = self._dapi_prefetch_key
        self._dapi_prefetch_future = None
        self._dapi_prefetch_key = None
        if future.cancelled():
            return False
        try:
            result = future.result()
        except Exception as exc:  # noqa: BLE001
            print(f"LOG: seam_live_prefetch_failed {exc!r}", flush=True)
            return False
        if key and result is not None:
            self._dapi_live_cache[key] = Path(result)
            print(f"LOG: seam_live_prefetch_ready slice={key[0]}", flush=True)
            self._apply_harvested_dapi_if_current(key, Path(result))
            return True
        return False

    def _apply_harvested_dapi_if_current(
        self, key: tuple[str, str], result: Path
    ) -> None:
        """Replace the temporary raw DAPI display when its worker completes."""
        toggle = getattr(self, "seam_channel_toggle", None)
        combo = getattr(self, "channel_combo", None)
        if toggle is None or combo is None or not toggle.isChecked():
            return
        if self._current_slice_id() != key[0]:
            return
        index = combo.currentIndex()
        if index < 0 or index >= len(self.channel_sources):
            return
        name, source_path = self.channel_sources[index]
        if not self._is_dapi_channel(name):
            return
        if self._dapi_live_cache_key(key[0], source_path) != key:
            return
        self.switch_channel(result, name, display_path=source_path)
        print(f"LOG: seam_live_prefetch_applied slice={key[0]}", flush=True)
        self._schedule_adjacent_dapi_prefetch()

    def _cancel_dapi_prefetch(self) -> None:
        # A completed worker result is valid for the whole Viewer session.
        # Collect it before invalidating only the *pending* queue generation.
        self._harvest_completed_dapi_prefetch()
        self._dapi_prefetch_generation += 1
        self._dapi_prefetch_queue = []
        future = self._dapi_prefetch_future
        if future is not None and not future.done():
            future.cancel()

    def _dapi_channel_for_pair(self, index: int):
        if index < 0 or index >= len(self.pairs):
            return None
        slice_id = self.pairs[index][2]
        for name, path in self._preview_channel_index.get(slice_id, []):
            if self._is_dapi_channel(name):
                return slice_id, Path(path), name
        return None

    def _schedule_adjacent_dapi_prefetch(self) -> None:
        toggle = getattr(self, "seam_channel_toggle", None)
        if toggle is None or not toggle.isChecked():
            return
        self._cancel_dapi_prefetch()
        generation = self._dapi_prefetch_generation
        candidates = []
        # Prefer the next section, then prepare Previous as a secondary cache.
        for index in (self.current_index + 1, self.current_index - 1):
            candidate = self._dapi_channel_for_pair(index)
            if candidate is None:
                continue
            slice_id, path, name = candidate
            if self._cached_dapi_live_path(slice_id, path) is None:
                candidates.append((slice_id, path, name))
        self._dapi_prefetch_queue = candidates
        if candidates:
            QTimer.singleShot(
                250,
                lambda generation=generation: self._start_dapi_prefetch(generation),
            )

    def _start_dapi_prefetch(self, generation: int) -> None:
        if generation != self._dapi_prefetch_generation:
            return
        if self._dapi_prefetch_future is not None and self._dapi_prefetch_future.done():
            self._harvest_completed_dapi_prefetch()
        if self._dapi_prefetch_future is not None:
            return
        if not self._dapi_prefetch_queue:
            return
        slice_id, path, _name = self._dapi_prefetch_queue.pop(0)
        key = self._dapi_live_cache_key(slice_id, path)
        if self._cached_dapi_live_path(slice_id, path) is not None:
            self._start_dapi_prefetch(generation)
            return
        digest = hashlib.sha256("\0".join(key).encode("utf-8")).hexdigest()
        dest = Path(self._dapi_live_cache_tempdir.name) / f"{digest}.png"
        self._dapi_prefetch_key = key
        self._dapi_prefetch_future = self._dapi_prefetch_executor.submit(
            self._compute_seam_live_for,
            path,
            slice_id,
            dest,
            prefetch=True,
        )
        QTimer.singleShot(
            50,
            lambda generation=generation: self._collect_dapi_prefetch(generation),
        )

    def _collect_dapi_prefetch(self, generation: int) -> None:
        future = self._dapi_prefetch_future
        if future is None:
            return
        if not future.done():
            QTimer.singleShot(
                50,
                lambda generation=generation: self._collect_dapi_prefetch(generation),
            )
            return
        if self._harvest_completed_dapi_prefetch() and generation == self._dapi_prefetch_generation:
            QTimer.singleShot(
                100,
                lambda generation=generation: self._start_dapi_prefetch(generation),
            )

    def _refresh_seam_toggle_state(self):
        toggle = self.__dict__.get("seam_channel_toggle")
        if toggle is None:
            return
        path = getattr(self, "active_channel_display_path", None) or getattr(
            self, "active_channel_path", None
        )
        if path is None and self.channel_sources:
            path = self.channel_sources[self.channel_combo.currentIndex()][1]
        toggle.blockSignals(True)
        # Always enabled once there is a valid channel path -- unlike the old
        # BaSiC-only toggle (which disabled itself when no precomputed
        # sibling existed), a missing precomputed file is now handled by
        # _on_seam_channel_toggled()'s live-compute prompt rather than by
        # disabling the checkbox.
        toggle.setEnabled(path is not None)
        toggle.blockSignals(False)
        self._update_seam_mode_label(path)

    _SEAM_TOGGLE_BASE_TOOLTIP = "Show a live seam-corrected DAPI/channel image."

    def _update_seam_mode_label(self, path) -> None:
        """Reflect the live mode this slice will use in the Seam correction
        button's own tooltip (2026-09-19 user request: previously a second
        QLabel row ("Known-geometry"/"Grid-estimated") stacked under the
        button -- moved into the tooltip instead so the button can be a
        normal single-row control, matching the header toolbar's other
        buttons in height).

        A Process output is intentionally not consulted here: Adjustment
        Viewer always computes its own live result so this tooltip and the
        displayed correction share one mode decision.
        """
        toggle = getattr(self, "seam_channel_toggle", None)
        if toggle is None:
            return
        if not toggle.isChecked():
            toggle.setToolTip(self._SEAM_TOGGLE_BASE_TOOLTIP)
            return
        mode_label = "Known-geometry"
        if path is not None:
            try:
                import seam_correct

                *_context, grid = self._live_seam_context(Path(path), seam_correct)
                if grid is None:
                    mode_label = "Grid-estimated"
            except Exception as exc:  # noqa: BLE001
                print(f"LOG: seam_mode_label_failed {exc!r}", flush=True)
                mode_label = "Grid-estimated"
        toggle.setToolTip(
            f"{self._SEAM_TOGGLE_BASE_TOOLTIP}\n"
            f"Mode: {mode_label} (imported seamgrid when available, "
            "otherwise grid-estimated)."
        )

    def _apply_seam_correction(self, path, name, *, interactive: bool) -> None:
        """Show the seam-corrected image for (path, name); shared by the
        interactive checkbox click (interactive=True, from
        _on_seam_channel_toggled()) and by the silent re-application used
        when the toggle is already checked and the view moves to a
        different section (interactive=False, from
        rebuild_channel_combo()/_load_section_at()/prev_image()/
        next_image()).

        The live-mode informational popup is shown once per
        Adjustment Viewer session (2026-09-08 user request: "안내 팝업은
        adjustment view가 켜지고 최초 1회만"): once shown, later calls with
        interactive=True go straight to live computation without asking
        again. A silent (interactive=False) call -- i.e. moving to another
        section with the toggle already checked -- never prompts at all,
        not even the first time (2026-09-08: "다른 섹션으로 이동시 ...
        자동으로 seam correction 적용, 안내 팝업x"), since the user already
        opted in by checking the box."""
        path = Path(path)
        if interactive and not getattr(self, "_seam_notice_shown", False):
            self._seam_notice_shown = True
            try:
                import seam_correct

                *_context, grid = self._live_seam_context(path, seam_correct)
                mode_label = "Known-geometry" if grid is not None else "Grid-estimated"
            except Exception as exc:  # noqa: BLE001
                print(f"LOG: seam_live_notice_mode_failed {exc!r}", flush=True)
                mode_label = "Grid-estimated"
            QMessageBox.information(
                self,
                "Seam correction",
                f"{mode_label} seam correction is available for this slice.",
            )
        # Do not use a Process-generated PNG here.  The checkbox always
        # displays a result computed with this slice's currently resolved
        # Known-geometry/Grid-estimated live mode.
        slice_id = self._current_slice_id()
        is_dapi = self._is_dapi_channel(name)
        live_path = self._cached_dapi_live_path(slice_id, path) if is_dapi else None
        if live_path is not None:
            print(f"LOG: seam_live_cache_hit slice={slice_id}", flush=True)
        else:
            key = self._dapi_live_cache_key(slice_id, path)
            future = self._dapi_prefetch_future
            if (
                is_dapi
                and future is not None
                and not future.done()
                and self._dapi_prefetch_key == key
            ):
                # Do not duplicate an in-flight adjacent DAPI correction.
                # Keep the raw image responsive; _harvest... replaces it when
                # the same worker result becomes available.
                print(f"LOG: seam_live_prefetch_wait slice={slice_id}", flush=True)
                self.switch_channel(path, name)
                return
            live_path = self._compute_seam_live(path)
            if live_path is not None and is_dapi:
                self._dapi_live_cache[self._dapi_live_cache_key(slice_id, path)] = live_path
        if live_path is None:
            if interactive:
                QMessageBox.warning(
                    self,
                    "Seam correction",
                    "Real-time seam correction failed. Please check the application log.",
                )
            self.seam_channel_toggle.blockSignals(True)
            self.seam_channel_toggle.setChecked(False)
            self.seam_channel_toggle.blockSignals(False)
            self.switch_channel(path, name)
            return
        # Seam state and resolved mode are shown beside the checkbox; retain
        # the selected channel's own name in the header.
        self.switch_channel(live_path, name, display_path=path)
        if is_dapi:
            self._schedule_adjacent_dapi_prefetch()

    def _on_seam_channel_toggled(self, checked: bool):
        if self.channel_combo.currentIndex() < 0:
            return
        name, path = self.channel_sources[self.channel_combo.currentIndex()]
        # The label starts hidden while Seam correction is off.  Refresh it
        # after the checkable button's state changes so the resolved mode is
        # visible directly below the now-active button.
        self._update_seam_mode_label(path)
        if not checked:
            self._cancel_dapi_prefetch()
            self.switch_channel(path, name)
            return
        self._apply_seam_correction(path, name, interactive=True)

    def _on_channel_combo_changed(self, index: int):
        if index < 0 or index >= len(self.channel_sources):
            return
        name, path = self.channel_sources[index]
        if not self._is_dapi_channel(name):
            self._cancel_dapi_prefetch()
        self._refresh_seam_toggle_state()
        # `_refresh_seam_toggle_state()` may still see the previous active
        # pixmap at this point; use the newly selected channel for the label.
        self._update_seam_mode_label(path)
        if getattr(self, "seam_channel_toggle", None) and self.seam_channel_toggle.isChecked():
            # Keep the user's Seam corrected choice across channel changes
            # and apply the new channel's live correction without a popup.
            self._apply_seam_correction(path, name, interactive=False)
            return
        self.switch_channel(path, name)


    def _update_paint_target_strip(self):
        """Refresh paint-target summary row (swatch, name, tier, adjustment, brush)."""
        if not hasattr(self, "paint_swatch"):
            return
        if self.selected_region_id is None:
            self.paint_swatch.setStyleSheet("background-color: #cccccc;")
            self.paint_target_name.setText("None")
            self.paint_target_name.setToolTip("")
            self.paint_target_fullname.setText("")
        else:
            rid = int(self.selected_region_id)
            r, g, b = resolve_label_color(rid, self.structure_map, self.catalog)
            self.paint_swatch.setStyleSheet(
                f"background-color: rgb({r}, {g}, {b});"
            )
            self.paint_target_name.setText(self.selected_region_name)
            self.paint_target_name.setToolTip(self._region_tooltip(rid))
            full_name = self._region_full_name(rid)
            # Skip the row when the full name is just a repeat of the acronym
            # line (e.g. selected_region_name already reads "VISp — Primary
            # visual area", or a full-detail-tier id with no separate long name).
            if full_name and full_name not in self.selected_region_name:
                self.paint_target_fullname.setText(full_name)
            else:
                self.paint_target_fullname.setText("")

        if self.ccf_advanced and self.level_combo.count() > 0:
            tier_ctx = self.level_combo.currentText()
        elif self.tier_combo.count() > 0:
            tier_ctx = self.tier_combo.currentText()
        else:
            tier_ctx = ""
        self.paint_tier_context.setText(tier_ctx)

        if self.allow_adjustment.isChecked():
            self.paint_adjust_badge.setText("ON")
            self.paint_adjust_badge.setStyleSheet("color: green; font-weight: bold;")
        else:
            self.paint_adjust_badge.setText("OFF")
            self.paint_adjust_badge.setStyleSheet("color: gray;")

        self.brush_label.setText(f"Brush {self.brush_size}")

    def _update_ring_color_swatch(self):
        btn = getattr(self, "ring_color_button", None)
        if btn is None:
            return
        btn.setStyleSheet(
            f"background-color: {self.brush_cursor_color.name()}; border: 1px solid #444;"
        )

    def _pick_brush_ring_color(self):
        color = QColorDialog.getColor(
            self.brush_cursor_color, self, "Brush ring color"
        )
        if color.isValid():
            self.brush_cursor_color = color
            self._update_ring_color_swatch()
            self._refresh_brush_cursor_pen()

    def _on_brush_ring_width_changed(self, value):
        self.brush_cursor_width = int(value)
        self._refresh_brush_cursor_pen()

    def _on_brush_ring_use_region_toggled(self, checked):
        self.brush_cursor_use_region = bool(checked)
        if getattr(self, "ring_color_button", None) is not None:
            self.ring_color_button.setEnabled(not checked)
        self._refresh_brush_cursor_pen()

    def _brush_ring_pen(self):
        """Pen for the brush cursor ring: region color if selected, else custom."""
        if self.brush_cursor_use_region and self.selected_region_id is not None:
            rgb = resolve_label_color(
                int(self.selected_region_id), self.structure_map, self.catalog
            )
            color = QColor(*rgb)
        else:
            color = QColor(self.brush_cursor_color)
        color.setAlpha(255)
        pen = QPen(color, self.brush_cursor_width)
        pen.setCosmetic(True)
        return pen

    def _refresh_brush_cursor_pen(self):
        """Re-apply the ring pen to the existing cursor items immediately."""
        try:
            pen = self._brush_ring_pen()
            self._brush_cursor_img.setPen(pen)
            self._brush_cursor_anno.setPen(pen)
        except Exception:
            pass

    def _update_brush_cursor(self, view, scene_point):
        """Show brush-size ring at cursor when adjustment is enabled."""
        hide_both = (
            not self.allow_adjustment.isChecked()
            or self.selected_region_id is None
            or self._space_down or self._is_panning
        )
        if hide_both:
            self._brush_cursor_img.setVisible(False)
            self._brush_cursor_anno.setVisible(False)
            return

        r = self.brush_size
        rect_x = scene_point.x() - r
        rect_y = scene_point.y() - r
        diameter = 2 * r

        pen = self._brush_ring_pen()
        brush = QBrush(Qt.BrushStyle.NoBrush)

        for item, active_view in (
            (self._brush_cursor_img, self.img_view),
            (self._brush_cursor_anno, self.anno_view),
        ):
            item.setRect(rect_x, rect_y, diameter, diameter)
            item.setPen(pen)
            item.setBrush(brush)
            item.setVisible(view is active_view)

    def _hide_brush_cursor(self) -> None:
        for item in (self._brush_cursor_img, self._brush_cursor_anno):
            item.setVisible(False)

    def _is_inside_dapi_image(self, scene_point) -> bool:
        """Whether *scene_point* lies on the displayed DAPI/channel pixels.

        The annotation map can be larger than an aspect-ratio-preserved DAPI
        pixmap.  Painting in that map-only margin must be ignored rather than
        converted into an array coordinate.
        """
        if scene_point is None or self.img_pixmap is None or self.img_pixmap.isNull():
            return False
        x, y = scene_point.x(), scene_point.y()
        return (
            0 <= x < self.img_pixmap.width()
            and 0 <= y < self.img_pixmap.height()
            and 0 <= x < self.current_label.shape[1]
            and 0 <= y < self.current_label.shape[0]
        )

    def _is_inside_compare_image(self, image_point) -> bool:
        """Bounds-only check for whether *image_point* lands on the Compare
        Adjacent picture's own drawn area (its native size, positioned at
        _compare_pixmap_offset -- the picture's own top-left corner in
        scene coordinates, which is not always the scene origin; see
        _enter_compare_mode()'s item_x/item_y), without the status-bar
        message or array lookup that _compare_adjacent_label_at() also
        does. Split out (2026-09-23 user report) so _pointer_in_pane_
        bounds() can ask "is this point on the picture" for painting/hover
        gates too, not just the right-click lookup that used to be the only
        caller of this bounds math.
        """
        offset_x, offset_y = self._compare_pixmap_offset
        native_w, native_h = self._compare_pixmap_native_size
        pic_x = image_point.x() - offset_x
        pic_y = image_point.y() - offset_y
        return (
            native_w > 0
            and native_h > 0
            and 0 <= pic_x < native_w
            and 0 <= pic_y < native_h
        )

    def _pointer_in_pane_bounds(self, view, image_point) -> bool:
        """Whether *image_point* (current_label-space coordinates, i.e.
        what view_to_image_coordinates() returns) lands on real, hoverable/
        clickable pixel content for *view*'s pane right now. Single shared
        gate for painting, the hover status bar, and right-click paint-
        target selection (2026-09-23 user report): while Compare Adjacent
        shows a reference slice whose native image is larger than
        current_label's own box, the Annotation pane's real content area is
        that reference picture's own extent, not current_label's -- using
        current_label's bounds unconditionally (_is_inside_dapi_image(),
        still correct for the DAPI pane and for the Annotation pane outside
        Compare Adjacent) made every caller reject valid points in that
        larger margin before they ever reached the Compare Adjacent-aware
        code that already existed for them (_compare_adjacent_label_at()).
        This turned out to affect not just the hover status bar but also
        _select_paint_target_at_view_pos()'s own initial guard -- both are
        routed through this one method now so a future fix only has to
        happen in one place.

        Uses the picture's own native bounds (not the padded canvas) for
        Compare Adjacent (2026-09-23, reconsidered same-day follow-up): an
        earlier revision of this method used the padded canvas so the
        black margin around a smaller reference image would count as a
        selectable/hoverable Lost in Warp area -- reverted, since that
        margin is not a real warped-out area, just "no reference image
        here". It now falls outside this pane's bounds like any other
        dead space, so it gets the same "Outside the adjacent slice's
        image" treatment as being off the canvas entirely -- see
        _is_inside_compare_image() and _compare_adjacent_label_at().
        """
        if self._compare_mode_active and view is self.anno_view:
            return self._is_inside_compare_image(image_point)
        return self._is_inside_dapi_image(image_point)

    def _configure_dual_views(self):
        """Center alignment, viewport-center zoom anchors, linked scrollbars."""
        for view in (self.img_view, self.anno_view):
            view.setAlignment(Qt.AlignmentFlag.AlignCenter)
            view.setTransformationAnchor(
                QGraphicsView.ViewportAnchor.AnchorViewCenter
            )
            view.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorViewCenter)
            view.setDragMode(QGraphicsView.DragMode.NoDrag)
            view.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
            view.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)
            view.setHorizontalScrollBarPolicy(
                Qt.ScrollBarPolicy.ScrollBarAsNeeded
            )
            view.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        for view in (self.img_view, self.anno_view):
            view.horizontalScrollBar().valueChanged.connect(
                lambda _v, src=view: self._sync_scroll_from(src)
            )
            view.verticalScrollBar().valueChanged.connect(
                lambda _v, src=view: self._sync_scroll_from(src)
            )

    def _refresh_pan_margins(self):
        """Recompute and cache each pane's virtual-pan margins.

        Only called from actual resize/zoom entry points
        (_refresh_virtual_pan_scene_rects() -- splitter drag and startup --
        and _apply_zoom()), never from a per-render path. Each pane's own
        viewport size is used (img_view/anno_view can differ when the
        splitter isn't centred), matching what _lock_scene_rect_to_pixmap()
        computed inline before this cache existed.
        """
        scale = max(self.zoom_level / 100.0, 0.01)
        for view, attr in (
            (self.img_view, "_pan_margin_img"),
            (self.anno_view, "_pan_margin_anno"),
        ):
            vp = view.viewport().size()
            setattr(
                self,
                attr,
                (max(64.0, vp.width() / scale), max(64.0, vp.height() / scale)),
            )

    def _lock_scene_rect_to_pixmap(self, scene: QGraphicsScene, pixmap: QPixmap):
        """Set a centred virtual canvas so fit-to-window images can pan.

        A scene rect equal to a small pixmap has no scrollbar range and Qt's
        AlignCenter pins the image in place.  Equal virtual margins retain the
        centred starting position while giving both axes enough scene space for
        drag-pan at every zoom level.

        Uses the cached per-pane margins from _refresh_pan_margins() instead
        of querying view.viewport().size() live (2026-09-19 user report: the
        slice still moved up/down on LUT slider drags and Seam Correction
        toggles even with the setSceneRect no-op guard below). Both of those
        actions re-render on every slider tick / toggle, and if the
        toolbar/sidebar layout is mid-pass at that exact moment (a label's
        text changed elsewhere in the same window, a style repolish, etc.),
        a live viewport().size() query can transiently read a pixel or two
        off from its settled value -- which the equality guard below would
        then treat as a genuine resize and let the setSceneRect() through.
        Margins only change on an explicit resize (splitter drag, zoom
        change) now, so this render path can no longer observe that kind of
        transient jitter at all.

        Also skips the actual setSceneRect() call when the computed rect
        already matches the scene's current one (2026-09-18 user report:
        Refresh, Undo, the LUT sliders, and the Seam Correction toggle --
        none of which change the pixmap's pixel size or the zoom level --
        each nudged the slice's on-screen position by a pixel or two). Qt
        recomputes the scrollbar range whenever setSceneRect() is called at
        all, even to an identical value, and that requantization is what was
        producing the drift. Real resizes (zoom change, splitter drag, an
        actual pixmap size change on channel/slice navigation) still differ
        from the current rect and are unaffected by this guard.
        """
        if pixmap is None or pixmap.isNull():
            return
        margin_x, margin_y = (
            self._pan_margin_img if scene is self.img_scene else self._pan_margin_anno
        )
        target_rect = QRectF(
            -margin_x,
            -margin_y,
            pixmap.width() + 2 * margin_x,
            pixmap.height() + 2 * margin_y,
        )
        current_rect = scene.sceneRect()
        if (
            abs(current_rect.left() - target_rect.left()) < 0.5
            and abs(current_rect.top() - target_rect.top()) < 0.5
            and abs(current_rect.width() - target_rect.width()) < 0.5
            and abs(current_rect.height() - target_rect.height()) < 0.5
        ):
            return
        scene.setSceneRect(target_rect)

    def _center_linked_views(self, center_scene_pos: QPointF):
        """Center both panes on one shared scene coordinate.

        A splitter gives each view a different viewport width, so their
        scrollbar ranges cannot be copied numerically.  Scene coordinates are
        the common representation that keeps the same tissue point centred.
        """
        self._syncing_scroll = True
        try:
            for view in (self.img_view, self.anno_view):
                view.centerOn(center_scene_pos)
        finally:
            self._syncing_scroll = False

    def _refresh_virtual_pan_scene_rects(self):
        """Resize virtual pan margins after the draggable splitter moves
        (including the splitter resize the Annotation map toggle triggers
        when it hides/restores that pane, via _reset_image_splitter_equal()
        -> QSplitter.setSizes() -> the splitterMoved signal this is wired
        to).

        2026-09-19 user report: toggling Annotation map drifted the slice's
        position -- on top of whatever repositioning this splitter resize
        genuinely calls for (a real viewport width change on the side that
        did resize does need a fresh centerOn(), unlike the pure re-render
        cases _apply_lut_to_display()/show_image_with_overlay() fixed with
        a saved-scrollbar restore -- there's no old value to restore here,
        the scrollbar range itself changed). But _center_linked_views()
        below unconditionally recenters *both* views from the same one
        captured *img_view* point, even a view whose own size didn't
        actually change -- and that unnecessary recentring is exactly the
        same rounding-prone centerOn() remapping _sync_scroll_from() was
        just fixed for. Saves each view's scrollbar values first and
        restores them verbatim afterward for whichever view's viewport
        size turns out to be unchanged, undoing that recentring's rounding
        for it while leaving the view that did resize with its freshly
        computed (and necessary) position.
        """
        if self.img_pixmap is None or self.img_pixmap.isNull():
            return
        prev_sizes = {
            view: view.viewport().size() for view in (self.img_view, self.anno_view)
        }
        # 2026-09-23 user report: after zooming into the first slice and
        # pressing Next, the scrollbars stayed the right *size* (see the
        # scrollbar-size unification fix earlier this session) but the
        # image itself ended up pinned to the top-left instead of centred,
        # even though both scrollbar handles sat in the middle of their
        # tracks. Root cause: this function is no longer reached only from
        # a splitter drag (its original, only caller) -- eventFilter()'s
        # Resize handler now also debounces into it for a paint_dock/main-
        # window resize (added the same day, to fix pan margins going
        # stale across the deferred Options-dock-width settle -- see that
        # handler's own comment). For a splitter drag, a view whose own
        # viewport size didn't change also has unchanged pan margins (they
        # are a pure function of viewport size and zoom, both unchanged),
        # so its scene rect doesn't move either, and its just-captured
        # prev_scroll value is still exactly valid to restore. But when
        # THIS function runs from a dock/window resize, Qt may have
        # already resized the viewport and auto-recentred it (via
        # AnchorViewCenter) using the *old*, not-yet-refreshed pan-margin
        # scene rect before this function ever got a chance to run --
        # prev_scroll then reflects "centred relative to a scene rect that
        # is about to change size", not "centred relative to the current
        # one". _refresh_pan_margins() + _lock_scene_rect_to_pixmap()
        # below can then genuinely grow/shrink that scene rect even though
        # the viewport's pixel *size* alone looks unchanged across this
        # one function call -- and blindly restoring prev_scroll in that
        # case reintroduces exactly the top-left bias this comment is
        # about (an old scrollbar value keeps its old absolute number, but
        # the range it's read against just changed, so the same number now
        # sits close to one end of the new, larger range instead of the
        # middle). Capturing each scene's rect here too, and only treating
        # a view as "unchanged" when its rect also didn't move, closes
        # that gap without touching the splitter-drag behavior at all
        # (there, the rect genuinely doesn't move for an unchanged view,
        # so this extra condition is always already satisfied).
        prev_rects = {
            self.img_view: self.img_scene.sceneRect(),
            self.anno_view: self.anno_scene.sceneRect(),
        }
        prev_scroll = {
            view: (
                view.horizontalScrollBar().value(),
                view.verticalScrollBar().value(),
            )
            for view in (self.img_view, self.anno_view)
        }
        self._refresh_pan_margins()
        if self._pending_pan_anchor is not None:
            # Re-target the scene point captured before the Annotation map
            # toggle burst began, rather than trusting a live read of
            # img_view now -- see _pending_pan_anchor's declaration comment.
            center = self._pending_pan_anchor
            self._pending_pan_anchor = None
        else:
            center = self._viewport_center_scene_pos(self.img_view)
        self._lock_scene_rect_to_pixmap(self.img_scene, self.img_pixmap)
        anno_pixmap = self._anno_pixmap()
        if anno_pixmap is not None and not anno_pixmap.isNull():
            self._lock_scene_rect_to_pixmap(self.anno_scene, anno_pixmap)
        self._center_linked_views(center)
        scene_for = {self.img_view: self.img_scene, self.anno_view: self.anno_scene}
        unchanged = [
            view
            for view in (self.img_view, self.anno_view)
            if view.isVisible()
            and view.viewport().size() == prev_sizes[view]
            and scene_for[view].sceneRect() == prev_rects[view]
        ]
        if unchanged:
            self._syncing_scroll = True
            try:
                for view in unchanged:
                    h_value, v_value = prev_scroll[view]
                    view.horizontalScrollBar().setValue(h_value)
                    view.verticalScrollBar().setValue(v_value)
            finally:
                self._syncing_scroll = False

    def _viewport_center_scene_pos(self, view: QGraphicsView) -> QPointF:
        vp = view.viewport()
        return view.mapToScene(vp.rect().center())

    def _apply_zoom(self, zoom_percent: int, *, center_scene_pos: QPointF | None = None):
        """Scale both panes, keeping the scene point under the viewport center.

        show_image_with_overlay() calls this on every single overlay rebuild
        (LUT slider ticks, Seam Correction toggle, Auto/Reset, Refresh,
        Undo -- not just actual zoom changes) purely to restore the
        viewport's pan position afterward. Re-issuing view.setTransform()
        with a numerically identical matrix on every one of those calls
        still forces Qt to fully reset each view's scene<->viewport mapping
        state; the centerOn() that immediately follows then re-derives a
        scrollbar position from that freshly-reset state, which can round
        to a different achievable integer scrollbar value than what was
        already showing even for the exact same target scene point --
        producing the up/down drift the pan-margin caching and setSceneRect
        no-op guard (both above) did not (2026-09-19 user report: LUT
        slider, Seam Correction toggle, and now Auto/Reset all still
        drifted). Skipping setTransform() entirely when the requested zoom
        already matches self.zoom_level removes this: only centerOn() runs,
        which does not reset the transform first.
        """
        zoom_percent = max(50, min(1000, int(zoom_percent)))
        if center_scene_pos is None:
            center_scene_pos = self._viewport_center_scene_pos(self.img_view)
        if zoom_percent != self.zoom_level:
            scale = zoom_percent / 100.0
            transform = QTransform()
            transform.scale(scale, scale)
            self._syncing_scroll = True
            try:
                for view in (self.img_view, self.anno_view):
                    view.setTransform(transform)
            finally:
                self._syncing_scroll = False
            self._refresh_pan_margins()
        self._center_linked_views(center_scene_pos)
        self.zoom_level = zoom_percent
        self.zoom_label.setText(f"Zoom {self.zoom_level}%")
        if self.zoom_slider.value() != zoom_percent:
            self.zoom_slider.blockSignals(True)
            self.zoom_slider.setValue(zoom_percent)
            self.zoom_slider.blockSignals(False)

    def _sync_scroll_from(self, source: QGraphicsView):
        """Mirror the *other* pane onto whatever scene point *source*'s own
        scrollbars now show, without touching *source* itself.

        2026-09-19 user report: dragging the horizontal scrollbar directly
        also nudged the vertical position -- on the very same view the user
        was dragging, which shouldn't move at all from a horizontal-only
        drag. Root cause: this used to call _center_linked_views(), which
        unconditionally re-applies centerOn() to *both* views, including
        source. That re-derives source's own scrollbar values from its
        just-set position by mapping through the scene<->view transform,
        and that remapping can round to a value a pixel off from what the
        user's drag had just set (the same rounding
        _apply_lut_to_display()/show_image_with_overlay() already had to
        work around elsewhere) -- even though source's position was
        already exactly correct and needed no recentring at all. Only the
        other view actually needs it.
        """
        if self._syncing_scroll:
            return
        other = self.anno_view if source is self.img_view else self.img_view
        center = self._viewport_center_scene_pos(source)
        self._syncing_scroll = True
        try:
            other.centerOn(center)
        finally:
            self._syncing_scroll = False

    def _set_img_pixmap(self, pixmap: QPixmap):
        if self._img_pixmap_item is not None:
            self.img_scene.removeItem(self._img_pixmap_item)
        self._img_pixmap_item = self.img_scene.addPixmap(pixmap)
        self._img_pixmap_item.setZValue(0)
        self._lock_scene_rect_to_pixmap(self.img_scene, pixmap)

    def _set_img_overlay_layer(self, pixmap: QPixmap | None):
        """Keep annotation as a separate composited graphics item."""
        if self._img_overlay_item is None:
            self._img_overlay_item = EditableRaster(pixmap or QPixmap())
            self.img_scene.addItem(self._img_overlay_item)
            self._img_overlay_item.setZValue(1)
        elif pixmap is not None:
            self._img_overlay_item.setPixmap(pixmap)
        self._img_overlay_item.setOpacity(self.opacity / 255.0 if self.overlay_visible else 0.0)

    def _set_anno_pixmap(self, pixmap: QPixmap):
        self.anno_pixmap = pixmap
        if self._anno_pixmap_item is None:
            self._anno_pixmap_item = EditableRaster(pixmap)
            self.anno_scene.addItem(self._anno_pixmap_item)
        else:
            self._anno_pixmap_item.setPixmap(pixmap)
        self._anno_pixmap_item.setZValue(0)
        self._lock_scene_rect_to_pixmap(self.anno_scene, pixmap)

    def _set_compare_pixmap(self, pixmap: QPixmap):
        """Show the Compare Adjacent composite on its own item, stacked
        above _anno_pixmap_item instead of replacing it (2026-09-23 user
        report; see the comment on _compare_pixmap_item's init)."""
        if self._compare_pixmap_item is None:
            self._compare_pixmap_item = EditableRaster(pixmap)
            self.anno_scene.addItem(self._compare_pixmap_item)
        else:
            self._compare_pixmap_item.setPixmap(pixmap)
        self._compare_pixmap_item.setZValue(10)
        self._compare_pixmap_item.setVisible(True)
        # Lock the pan/scrollbar range to the REAL annotation pixmap's own
        # size, not to this compare item's own size (2026-09-23 user
        # report: the Annotation pane's scrollbar thumb size differed from
        # the DAPI pane's once Compare Adjacent was turned on, and stayed
        # different even after turning it back off). The adjacent slice's
        # native image can be larger than current_label's box (see the
        # native-size comment in _enter_compare_mode()), and locking the
        # scene rect to that larger size made the two panes' scrollable
        # ranges -- and therefore their scrollbar thumb sizes -- genuinely
        # diverge while comparing. A QGraphicsItem still renders in full
        # regardless of the scene's nominal rect, so this compare item is
        # still drawn at its own true size and position -- nothing here
        # crops or rescales the picture -- this only keeps both panes'
        # *pannable* range identical to what the DAPI pane already uses.
        # (The "persisted after turning off" half of the report was the
        # same root cause: show_image_with_overlay() -> _set_anno_pixmap()
        # does correctly re-lock the Annotation pane back to the small
        # real pixmap on exit, but only once refresh_drawings() actually
        # runs; anything that re-enters compare mode meanwhile --
        # _refresh_compare_if_active(), offset changes -- re-grows it via
        # this same call, so the only reliable fix is to never grow it in
        # the first place.) Falls back to this pixmap's own size only if
        # the real annotation pixmap isn't available yet.
        real_anno_pixmap = self._anno_pixmap()
        reference_pixmap = (
            real_anno_pixmap
            if real_anno_pixmap is not None and not real_anno_pixmap.isNull()
            else pixmap
        )
        self._lock_scene_rect_to_pixmap(self.anno_scene, reference_pixmap)

    def _hide_compare_pixmap(self):
        if self._compare_pixmap_item is not None:
            self._compare_pixmap_item.setVisible(False)

    def _display_overlay_pixmap(self) -> QPixmap:
        """Map label pixels into the exact image display raster before compositing."""
        current = self._anno_pixmap()
        if current is not None:
            self.anno_pixmap = current
        if self.anno_pixmap.isNull() or self.img_pixmap.isNull():
            return self.anno_pixmap
        target = self.img_pixmap.size()
        if self.anno_pixmap.size() == target:
            return self.anno_pixmap
        return self.anno_pixmap.scaled(
            target, Qt.AspectRatioMode.IgnoreAspectRatio,
            Qt.TransformationMode.FastTransformation,
        )

    def _anno_pixmap(self) -> QPixmap | None:
        if self._anno_pixmap_item is None:
            return None
        return self._anno_pixmap_item.pixmap()

    def _flatten_catalog_regions(self, query: str = "") -> list[dict]:
        if not self.catalog:
            return []
        q = query.strip().lower()
        out: list[dict] = []
        for node in self.catalog.get("by_id", {}).values():
            if not node:
                continue
            hay = (
                f"{node.get('acronym', '')} {node.get('name', '')} "
                f"{node.get('alias', '')}"
            ).lower()
            if not q or q in hay:
                out.append(node)
        out.sort(key=lambda n: str(n.get("acronym", "")))
        return out

    def _refresh_search_completer(self, query: str = ""):
        completer = self.area_search_box.completer()
        if completer is None:
            return
        regions = self._flatten_catalog_regions(query)
        q = (query or "").strip().lower()

        def sort_key(n):
            ac = str(n.get("acronym") or "").lower()
            if q and ac == q:
                return (0, ac)
            if q and ac.startswith(q):
                return (1, ac)
            return (2, ac)

        regions = sorted(regions, key=sort_key)
        # Keep the compact acronym-only Area combo, but make search results
        # self-explanatory by including each region's full name.
        strings = [self._region_display_text(n) for n in regions[:500]]
        from qtpy.QtCore import QStringListModel

        completer.setModel(QStringListModel(strings))

    def _on_area_search_box_edited(self, text: str):
        self._refresh_search_completer(text)

    def _commit_search_text(self, text: str):
        """Select paint target from Search text (full catalog, not tier-scoped)."""
        text = (text or "").strip()
        if not text or not self.catalog:
            return
        regions = self._flatten_catalog_regions("")
        q = text.lower()
        exact_display = None
        exact_acronym = None
        contains = None
        for node in regions:
            disp = self._region_display_text(node)
            ac = str(node.get("acronym") or "").lower()
            if disp.lower() == q:
                exact_display = node
                break
            if ac == q and exact_acronym is None:
                exact_acronym = node
            hay = (
                f"{node.get('acronym', '')} {node.get('name', '')} "
                f"{node.get('alias', '')}"
            ).lower()
            if contains is None and (q in hay or q in disp.lower()):
                contains = node
        node = exact_display or exact_acronym or contains
        if not node:
            return
        self.set_paint_region(node["id"])
        self._sync_area_combo_to_region(node["id"])
        disp = self._region_display_text(node)
        self.area_search_box.blockSignals(True)
        self.area_search_box.setText(disp)
        self.area_search_box.blockSignals(False)

    def _on_area_search_completer_activated(self, text: str):
        self._commit_search_text(text)

    def _section_has_annotation_labels(self) -> bool:
        if self.current_label is None:
            return False
        return bool(np.any(self.current_label != 0))

    def _maybe_warn_tier_change_mixed_map(self):
        if not self._section_has_annotation_labels():
            return
        msg = (
            "Changing the content tier and painting more regions can create a "
            "mixed-resolution annotation map. Downstream tools (especially Isolate "
            "Regions) may behave unexpectedly unless Include cortical layers and "
            "parcellation match your labels. Count Brain will roll up totals, but "
            "intensity PKLs may need a re-run."
        )
        self.status_bar.showMessage(msg, 20000)
        if is_suppressed(KEY_MIXED_RESOLUTION_TIER):
            return
        if not self._tier_change_notice_shown:
            self._tier_change_notice_shown = True
            dialog = QMessageBox(self)
            dialog.setIcon(QMessageBox.Icon.Information)
            dialog.setWindowTitle("Mixed-resolution labels")
            dialog.setText("Content tier changed after painting")
            dialog.setInformativeText(msg)
            dialog.setStandardButtons(QMessageBox.StandardButton.Ok)
            dont_show = QCheckBox("Don't show this warning again")
            dialog.setCheckBox(dont_show)
            dialog.exec()
            if dont_show.isChecked():
                set_suppressed(KEY_MIXED_RESOLUTION_TIER, True)

    def _region_picker_expanded(self) -> bool:
        """Whether the Region picker section is currently expanded.

        Reads the header QToolButton _make_group_collapsible() stashed on
        the group via its "collapsibleHeader" property, rather than
        tracking a second, separately-maintained boolean that could drift
        out of sync with it. Defaults to True (visible) if the group or its
        header aren't set up yet, so an early call during __init__ still
        behaves like the pre-collapsible-sidebar code did.
        """
        group = getattr(self, "_paint_controls_group", None)
        if group is None:
            return True
        header = group.property("collapsibleHeader")
        if header is None:
            return True
        return bool(header.isChecked())

    def _update_paint_resolution_warning(self):
        if not hasattr(self, "paint_resolution_warning"):
            return
        if self.current_label is None or not self.catalog:
            self.paint_resolution_warning.clear()
            self.paint_resolution_warning.setVisible(False)
            return
        from annotation_label_audit import audit_label_array
        from annotation_relabel import get_slice_parcellation

        entry = get_slice_parcellation(self.annotation_dir, self._current_slice_id())
        result = audit_label_array(
            self.current_label, self.catalog, self.structure_map, entry
        )
        messages = {
            "mixed_st_levels": (
                "Labels on this section mix multiple CCF levels — Isolate Regions "
                "may need Include cortical layers and a re-run."
            ),
            "layer_on_coarse_parcellation": (
                "Layer-level labels with coarse parcellation — enable Include "
                "cortical layers or re-parcellate at layers tier."
            ),
            "parcellation_metadata_mismatch": (
                "Painted labels do not match declared parcellation tier — re-run "
                "Parcellation or paint at one tier."
            ),
        }
        parts = [messages[c] for c in result.get("issues", []) if c in messages]
        if parts:
            self.paint_resolution_warning.setText(" ".join(parts))
            # Only actually show it while Region picker (this label's own
            # section) is expanded (2026-09-19 user report: clicking
            # Refresh made this label reappear even while that section was
            # still collapsed -- this unconditional setVisible(True) is
            # exactly what did it, since nothing here checked the section's
            # own collapsed state before). The warning is still computed
            # and its text kept current either way, so expanding the
            # section later shows it immediately without waiting for the
            # next audit.
            self.paint_resolution_warning.setVisible(self._region_picker_expanded())
        else:
            self.paint_resolution_warning.clear()
            self.paint_resolution_warning.setVisible(False)
        # Region picker uses a Fixed vertical size policy (see add_section()
        # in _OptionsSectionsContainer) so it never grows past its content's
        # sizeHint(). updateGeometry() alone (2026-09-23, first attempt)
        # turned out not to be enough: QBoxLayout only asks a child for its
        # real heightForWidth() using the *layout's* current effective
        # width, and a Fixed-policy QGroupBox's own sizeHint() does not
        # reliably re-derive that from this label's actual rendered width
        # after the fact -- so the box was still sized for fewer wrapped
        # lines than the label (still user-reported clipped) even after
        # invalidating the cached hint. Computing the wrapped height
        # directly from the label's own current width via QFontMetrics and
        # pinning it with setFixedHeight() sidesteps that negotiation
        # entirely: the label always reports its own exact real height as
        # its sizeHint(), which a Fixed-policy ancestor's sizeHint() sums
        # correctly regardless of any heightForWidth quirk.
        self._resize_paint_resolution_warning()
        group = getattr(self, "_paint_controls_group", None)
        if group is not None:
            group.updateGeometry()
        self._sync_options_list_heights()

    def _resize_paint_resolution_warning(self):
        """Pin paint_resolution_warning's height to what its current text
        actually needs at its current width -- see the comment at this
        method's only call site, _update_paint_resolution_warning()."""
        label = getattr(self, "paint_resolution_warning", None)
        if label is None:
            return
        if not label.isVisible() or not label.text():
            label.setMinimumHeight(0)
            label.setMaximumHeight(16777215)  # Qt's QWIDGETSIZE_MAX -- undo any prior cap
            return
        width = label.width()
        if width <= 0:
            group = getattr(self, "_paint_controls_group", None)
            width = (
                group.width() - 16
                if group is not None and group.width() > 0
                else 240
            )
        rect = label.fontMetrics().boundingRect(
            QRect(0, 0, max(width, 1), 0),
            int(Qt.TextFlag.TextWordWrap),
            label.text(),
        )
        label.setFixedHeight(rect.height() + 4)

    def _init_paint_region_controls(self):
        """Populate hierarchy/area combos from the CCF catalog."""
        if not self.catalog:
            self.tier_combo.setEnabled(False)
            self.level_combo.setEnabled(False)
            self.area_combo.setEnabled(False)
            self.ccf_advanced_toggle.setEnabled(False)
            return

        self.tier_combo.blockSignals(True)
        self.tier_combo.clear()
        self.tier_combo.addItem("Full detail", FULL_DETAIL_TIER)
        self.tier_combo.setItemData(
            0,
            "Show every available CCFv3 structure for direct painting.",
            Qt.ItemDataRole.ToolTipRole,
        )
        tiers = list_tiers(self.catalog)
        default_tier_index = 0
        for i, tier in enumerate(tiers):
            label = tier["label"]
            self.tier_combo.addItem(label, tier["id"])
            self.tier_combo.setItemData(
                i + 1, tier.get("description", ""), Qt.ItemDataRole.ToolTipRole
            )
            if tier["id"] == self.current_tier_id:
                default_tier_index = i + 1
        if self.current_tier_id == FULL_DETAIL_TIER:
            default_tier_index = 0
        self.tier_combo.setCurrentIndex(default_tier_index)
        self.tier_combo.blockSignals(False)
        self.current_tier_id = self.tier_combo.currentData() or "areas"

        self.level_combo.blockSignals(True)
        self.level_combo.clear()
        levels = list_ccf_levels(self.catalog)
        default_level_index = 0
        for i, info in enumerate(levels):
            label, tooltip = compact_ccf_level_label_and_tooltip(info)
            self.level_combo.addItem(label, info["level"])
            self.level_combo.setItemData(
                i, tooltip, Qt.ItemDataRole.ToolTipRole
            )
            if info["level"] == 6:
                default_level_index = i
        self.level_combo.setCurrentIndex(default_level_index)
        self.level_combo.blockSignals(False)
        self._show_current_combo_item_tooltip(self.level_combo)
        self._rebuild_area_combo()

    def _current_catalog_level(self) -> int | None:
        if self.level_combo.count() == 0:
            return None
        level = self.level_combo.currentData()
        return int(level) if level is not None else None

    def _current_tier_id(self) -> str | None:
        if self.tier_combo.count() == 0:
            return None
        data = self.tier_combo.currentData()
        return str(data) if data is not None else None

    def _region_display_text(self, node: dict) -> str:
        display_name = node.get("alias") or node["name"]
        return f"{node['acronym']} — {display_name}"

    def _region_picker_text(self, node: dict) -> str:
        """Compact Area text; the full region name is supplied as a tooltip."""
        return str(node.get("acronym") or node.get("name") or "Unknown region")

    @staticmethod
    def _show_current_combo_item_tooltip(combo: QComboBox):
        """Expose an item's own tooltip when hovering the closed combo box."""
        tooltip = combo.itemData(combo.currentIndex(), Qt.ItemDataRole.ToolTipRole)
        combo.setToolTip(str(tooltip or ""))

    def _region_full_name(self, region_id: int) -> str:
        """Full anatomical name for a region id, or "" if none is known.

        Same source/fallback order as _region_tooltip()'s name line: the
        catalog node's "name" field first (present for every tiered region),
        then structure_map's "name" (covers full-detail-tier ids that have no
        catalog node).
        """
        node = get_region(int(region_id), self.catalog) if self.catalog else None
        if node and node.get("name"):
            return str(node.get("alias") or node["name"])
        info = self.structure_map.get(np.uint32(region_id), {})
        return str(info.get("name") or "")

    def _region_tooltip(self, region_id: int) -> str:
        node = get_region(int(region_id), self.catalog) if self.catalog else None
        parts = (
            [str(node.get("alias") or node["name"])]
            if node and node.get("name")
            else []
        )
        info = self.structure_map.get(np.uint32(region_id), {})
        color = info.get("color")
        if color:
            parts.append(f"RGB{color}")
        return "\n".join(parts)

    def _current_regions(self, search_query: str = "") -> list[dict]:
        """Resolve current region list from either advanced level or semantic tier."""
        if not self.catalog:
            return []
        if self.ccf_advanced:
            level = self._current_catalog_level()
            if level is None:
                return []
            return list_regions_at_level(level, search_query, self.catalog)
        tier_id = self._current_tier_id()
        if not tier_id:
            return []
        if tier_id == FULL_DETAIL_TIER:
            query = (search_query or "").strip().lower()
            regions = list(self.catalog.get("nodes") or [])
            if query:
                regions = [
                    node
                    for node in regions
                    if query
                    in (
                        f"{node.get('acronym', '')} {node.get('name', '')} "
                        f"{node.get('alias', '')} "
                        f"{node.get('groupParentAcronym', '')}"
                    ).lower()
                ]
            return sorted(regions, key=lambda node: str(node.get("acronym", "")))
        return list_regions_for_tier(tier_id, self.catalog, search_query)

    def _rebuild_area_combo(self, search_query: str = "", select_id: int | None = None):
        if not self.catalog:
            return

        # Preserve previously selected paint region across tier/mode swaps.
        if select_id is None and self.selected_region_id is not None:
            select_id = int(self.selected_region_id)

        regions = self._current_regions(search_query)
        self._area_combo_updating = True
        self.area_combo.blockSignals(True)
        line_edit = self.area_combo.lineEdit()
        if line_edit is not None:
            line_edit.blockSignals(True)

        self.area_combo.clear()
        select_index = -1
        for i, node in enumerate(regions):
            display = self._region_picker_text(node)
            self.area_combo.addItem(display, node["id"])
            self.area_combo.setItemData(
                i,
                self._region_tooltip(node["id"]),
                Qt.ItemDataRole.ToolTipRole,
            )
            if select_id is not None and node["id"] == select_id:
                select_index = i

        if select_index >= 0:
            self.area_combo.setCurrentIndex(select_index)
            if line_edit is not None:
                line_edit.setText(self.area_combo.currentText())
        elif select_id is not None:
            # Keep paint target when id is outside the current tier/level list
            # (right-click pick, tier swap). Do not clobber to regions[0].
            self.area_combo.setCurrentIndex(-1)
            if line_edit is not None:
                node = get_region(int(select_id), self.catalog)
                if node:
                    line_edit.setText(self._region_picker_text(node))
                else:
                    line_edit.clear()
        elif regions and not search_query.strip():
            # Init only: no prior selection → default first region in list.
            self.area_combo.setCurrentIndex(0)
            if line_edit is not None:
                line_edit.setText(self.area_combo.currentText())
            self.set_paint_region(regions[0]["id"])

        if line_edit is not None:
            line_edit.blockSignals(False)
        self.area_combo.blockSignals(False)
        self._area_combo_updating = False

    def _on_level_changed(self, _index: int):
        self._show_current_combo_item_tooltip(self.level_combo)
        self._maybe_warn_tier_change_mixed_map()
        if self.ccf_advanced:
            self._rebuild_area_combo()
        self._refresh_search_completer(self.area_search_box.text())
        self._update_paint_target_strip()
        self._update_paint_resolution_warning()

    def _on_tier_changed(self, _index: int):
        self._maybe_warn_tier_change_mixed_map()
        tier_id = self._current_tier_id()
        if tier_id:
            self.current_tier_id = tier_id
        if not self.ccf_advanced:
            self._rebuild_area_combo()
        self._refresh_search_completer(self.area_search_box.text())
        self._update_paint_target_strip()
        self._update_paint_resolution_warning()

    def _on_ccf_advanced_toggled(self, checked: bool):
        self._maybe_warn_tier_change_mixed_map()
        self.ccf_advanced = bool(checked)
        self.tier_combo.setEnabled(not self.ccf_advanced)
        self.level_combo.setEnabled(self.ccf_advanced)
        self._rebuild_area_combo()
        self._refresh_search_completer(self.area_search_box.text())
        self._update_paint_target_strip()
        self._update_paint_resolution_warning()

    def _on_area_search_changed(self, text: str):
        if self._area_combo_updating or not self.catalog:
            return
        self._rebuild_area_combo(text)

    def _on_area_activated(self, index: int):
        if index < 0 or not self.catalog:
            return
        region_id = self.area_combo.itemData(index)
        if region_id is None:
            return
        self.set_paint_region(int(region_id))

    def set_paint_region(self, region_id, acronym=None, name=None):
        """Set the brush target region from catalog id."""
        region_id = int(region_id)
        previous_region_id = self.selected_region_id
        # The selection highlight is painted into the annotation raster for fast
        # display.  Restore the old region from the cached base raster before
        # changing targets, otherwise its pink pixels persist after a right-click
        # selects another brain area.
        if (
            self._overlay_ready
            and previous_region_id is not None
            and int(previous_region_id) != region_id
        ):
            self._restore_region_highlight(int(previous_region_id))
        self.selected_region_id = np.uint32(region_id)
        if acronym and name:
            self.selected_region_name = f"{acronym} — {name}"
        else:
            node = get_region(region_id, self.catalog) if self.catalog else None
            if node:
                self.selected_region_name = self._region_picker_text(node)
            else:
                if self.catalog:
                    self.status_bar.showMessage(
                        f"Catalog node missing for id {region_id}"
                    )
                info = self.structure_map.get(self.selected_region_id, {})
                self.selected_region_name = info.get("name", "Unknown region")
        if not _structure_map_entry(self.structure_map, region_id):
            self.status_bar.showMessage(
                f"No structure_map entry for id {region_id}"
            )
        self.area_combo.setToolTip(self._region_tooltip(region_id))
        if self._overlay_ready:
            self.repaint_selected_only()
        self._update_paint_target_strip()

    def _restore_region_highlight(self, region_id: int):
        """Restore one highlighted region from the unhighlighted RGBA cache.

        A tight rectangular patch avoids rebuilding the full annotation overlay
        when users switch the paint target with a right-click.
        """
        if (
            self._anno_rgba is None
            or self.current_label is None
            or self._anno_rgba.shape[:2] != self.current_label.shape
        ):
            return
        ys, xs = np.where(self.current_label == region_id)
        if not len(xs):
            return
        left, right = int(xs.min()), int(xs.max()) + 1
        top, bottom = int(ys.min()), int(ys.max()) + 1
        base_patch = np.ascontiguousarray(self._anno_rgba[top:bottom, left:right])
        anno_pixmap = self._anno_pixmap()
        if anno_pixmap is None or anno_pixmap.isNull():
            return
        painter = QPainter(anno_pixmap)
        painter.drawImage(left, top, numpy_array_to_qimage(base_patch))
        painter.end()
        self._set_anno_pixmap(anno_pixmap)
        self._set_img_overlay_layer(self._display_overlay_pixmap())

    def _tier_id_containing_region(self, region_id: int) -> str | None:
        """Finest semantic tier whose picker list contains region_id, or None."""
        if not self.catalog:
            return None
        rid = int(region_id)
        # Finest-first so laminar picks land on Cortical layers when possible.
        preference = ("layers", "parts", "subareas", "areas", "regions", "major")
        by_id = {t["id"]: t for t in list_tiers(self.catalog)}
        for tier_id in preference:
            tier = by_id.get(tier_id)
            if tier and rid in tier.get("region_ids", []):
                return tier_id
        return None

    def _sync_area_combo_to_region(self, region_id):
        """Align Hierarchy/Level and Area combo with a picked atlas id.

        Programmatic tier/level switches use blockSignals so the mixed-resolution
        warning only fires on user-driven combo changes.
        """
        if not self.catalog:
            return
        node = get_region(int(region_id), self.catalog)
        if not node:
            return
        rid = int(node["id"])
        if self.ccf_advanced:
            level = node["st_level"]
            for i in range(self.level_combo.count()):
                if self.level_combo.itemData(i) == level:
                    self.level_combo.blockSignals(True)
                    self.level_combo.setCurrentIndex(i)
                    self.level_combo.blockSignals(False)
                    break
        else:
            current = self._current_tier_id()
            tiers = list_tiers(self.catalog)
            current_ids: set[int] = set()
            for tier in tiers:
                if tier["id"] == current:
                    current_ids = set(tier.get("region_ids") or [])
                    break
            if rid not in current_ids:
                target = self._tier_id_containing_region(rid)
                if target and target != current:
                    for i in range(self.tier_combo.count()):
                        if self.tier_combo.itemData(i) == target:
                            self.tier_combo.blockSignals(True)
                            self.tier_combo.setCurrentIndex(i)
                            self.tier_combo.blockSignals(False)
                            self.current_tier_id = target
                            break
        self._show_current_combo_item_tooltip(self.level_combo)
        self._rebuild_area_combo(select_id=node["id"])

    def _current_slice_id(self) -> str:
        return self.pairs[self.current_index][2]

    def _make_group_collapsible(
        self, group, title=None, draggable=False, key=None, header_extra=None
    ):
        """Add a clickable header that expands/collapses the group's contents.
        Uses a QToolButton (not QGroupBox.setCheckable) so it never toggles child
        enabled state, which would clash with app-managed disables (e.g. catalog).

        *title* lets this be reused on a plain QWidget sub-section that has
        its own QVBoxLayout but no QGroupBox title of its own (2026-09-19
        user request: fold just the LUT black/white/gamma controls inside
        the "View" group, independent of Opacity/Zoom above them, rather
        than the whole group). When *title* is omitted, behavior is
        unchanged: the QGroupBox's own title is used and cleared.

        *draggable* uses _DraggableGroupHeader instead of a plain
        QToolButton, so the header can also be dragged to reorder this
        section within the Options sidebar's reorderable list (2026-09-19
        user request). Only the 5 top-level sections that actually live in
        that list pass this; the nested LUT sub-header does not (it isn't a
        list row, so there's nothing to reorder it against)."""
        layout = group.layout()
        if layout is None:
            return
        if title is None:
            # The 5 top-level sections always pass title= explicitly now
            # (2026-09-23: switched from QGroupBox to QFrame -- see the
            # comment below) -- this branch only remains as a fallback for
            # a QGroupBox-based caller that doesn't.
            title = group.title()
            group.setTitle("")
        if draggable:
            # Every one of the 5 top-level Options sidebar sections is
            # draggable, and only they get this bordered "tab" look, so
            # *draggable* doubles as the signal for it (2026-09-23,
            # replacing the second QGroupBox/title fix attempt): a
            # QGroupBox reserves title-bar height above its content
            # structurally, tied to the widget class itself rather than
            # to the title *text* -- an empty title string plus
            # `QGroupBox::title { height: 0px }` did not remove it in the
            # Qt style this app renders with (2026-09-23 user report: the
            # gap persisted unchanged after that attempt). These 5
            # sections were switched from QGroupBox to plain QFrame at
            # their construction sites specifically to sidestep this --
            # QFrame has no title concept at all, so there is nothing
            # calling for that reserved space in the first place.
            #
            # 2026-09-23 user report, 2nd follow-up (with a screenshot):
            # a bare, selector-less declaration list still isn't scoped to
            # just this one widget -- View's own border-box visibly nested
            # a *second* border box tightly around the LUT sub-section
            # (a plain QWidget, not even a QFrame, added inside View's
            # layout before this runs). Qt Style Sheets cascade "border"
            # down to unstyled descendants of whatever widget the sheet
            # was set on regardless of whether a selector is present --
            # the earlier switch away from a bare "QFrame { ... }" type
            # selector only stopped this rule from also matching OTHER
            # QFrame instances *elsewhere* in the window that happen to
            # share the type, it never stopped it from cascading into
            # this widget's own descendants. An ID selector scoped to a
            # unique objectName does not have that problem: Qt only
            # matches it against the exact (type, objectName) pair, so a
            # plain QWidget descendant like the LUT sub-section can never
            # match a "QFrame#<id>" rule at all, however deep in the tree
            # it lives.
            section_id = f"topLevelOptionsSection_{id(group)}"
            group.setObjectName(section_id)
            group.setStyleSheet(
                f"QFrame#{section_id} {{ border: 1px solid white; "
                "border-radius: 3px; margin: 0px; padding: 2px; }"
            )
        # Stable key for section-order persistence (drag-reorder wrapper
        # below) -- captured here since QGroupBox.title() is cleared right
        # above, and a plain QWidget sub-section (title= override) has no
        # title of its own at all. *key* lets this stay stable across a
        # display-title rename (2026-09-19: "Brush & edits" -> "Brush"),
        # since the saved order string in QSettings is keyed by whatever
        # this property held when the order was last saved. Defaults to
        # *title* for every other section, unchanged.
        group.setProperty("sectionKey", key if key is not None else title)
        if draggable:
            header = _DraggableGroupHeader(group, viewer=self)
            header.setToolTip("Click to fold/unfold. Drag to reorder.")
        else:
            header = QToolButton(group)
        header.setText("▾ " + title)
        header.setCheckable(True)
        header.setChecked(True)
        header.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        header.setStyleSheet(
            "QToolButton { border: none; font-weight: bold; padding: 2px; }"
        )
        header.setCursor(Qt.CursorShape.PointingHandCursor)
        # Lets other code (e.g. _update_paint_resolution_warning()) ask
        # whether this section is currently expanded without needing its
        # own reference to *header* threaded through separately.
        group.setProperty("collapsibleHeader", header)
        # *header_extra* places a small always-visible widget beside the
        # title -- e.g. Toggle Overlay next to the "Brush" section
        # (2026-09-19 user request: moved out of the top header toolbar so
        # it sits next to that section's title instead, visible even while
        # the section itself is collapsed). Kept out of the header's own
        # QToolButton so clicking it does not also fold/unfold the section.
        if header_extra is not None:
            header_row = QHBoxLayout()
            header_row.setContentsMargins(0, 0, 0, 0)
            header_row.setSpacing(4)
            header_row.addWidget(header, 1)
            header_row.addWidget(header_extra, 0)
            layout.insertLayout(0, header_row)
        else:
            header_row = None
            layout.insertWidget(0, header)
        header.toggled.connect(
            lambda on, g=group, h=header, t=title, hr=header_row: self._set_group_collapsed(
                g, h, t, on, header_row=hr
            )
        )

    def _set_group_collapsed(self, group, header, title, expanded, header_row=None):
        header.setText(("▾ " if expanded else "▸ ") + title)
        layout = group.layout()
        if layout is None:
            return
        # paint_resolution_warning manages its own visibility from
        # _update_paint_resolution_warning() (it should only ever show when
        # there's an actual warning to show, regardless of this section's
        # collapse state) -- the blanket "match every child to this
        # section's expand state" loop below would otherwise force it
        # visible on every expand (2026-09-19 user report: Refresh made
        # this label reappear even while Region picker was still collapsed
        # -- the blanket loop isn't even the direct cause there, that
        # label's own setVisible(True) call in the warning-refresh path
        # simply overrode the collapse state's own hide, since nothing
        # about setting a widget's visibility respects its ancestor's
        # collapsed state on its own -- but leaving it in the blanket loop
        # would introduce the same class of bug in the other direction,
        # forcing it visible on every expand even with nothing to warn
        # about, so it's excluded here and left entirely to that method).
        warning = getattr(self, "paint_resolution_warning", None)
        for i in range(layout.count()):
            item = layout.itemAt(i)
            widget = item.widget()
            if widget is header or widget is warning:
                continue
            if widget is not None:
                widget.setVisible(expanded)
                continue
            sub = item.layout()
            if sub is None or sub is header_row:
                # header_row (when header_extra was used) holds the header
                # itself plus its always-visible extra widget (e.g. Toggle
                # Overlay next to "Brush") -- neither should be hidden by
                # collapsing this section, so this sub-layout is skipped
                # entirely rather than having its children's visibility
                # toggled like an ordinary control row.
                continue
            for j in range(sub.count()):
                sub_widget = sub.itemAt(j).widget()
                if sub_widget is not None:
                    sub_widget.setVisible(expanded)
        if warning is not None and warning is not header:
            # Re-run its own visibility decision now that *expanded* (and
            # so group.property("collapsibleHeader").isChecked(), which it
            # reads) is up to date -- e.g. re-show it on expand if there is
            # in fact an active warning, or keep it hidden on collapse.
            self._update_paint_resolution_warning()
        self._sync_options_list_heights()

    def _wrap_options_sections_reorderable(self, layout: QVBoxLayout) -> "_OptionsSectionsContainer":
        """Replace the plain top-to-bottom stack of Options group boxes with
        an _OptionsSectionsContainer so the user can drag-reorder them
        (2026-09-19 user request, 2nd attempt -- ported from py/map.py's
        proven ReorderableSidebarSections, since the first attempt's
        QListWidget-based reordering bounced/glitched for the user),
        remembering the chosen order across sessions via
        self._options_settings. Only reparents the group boxes
        _init_paint_controls()/_init_parcellation_controls() already built
        and added to *layout* -- their own internal widgets/signals are
        untouched, so every self.xxx reference elsewhere in this class
        still points at the same live widget, just under a new parent."""
        sections = []
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                sections.append(widget)

        container = _OptionsSectionsContainer(self, self)
        container.setObjectName("OptionsSectionsContainer")

        by_key = {}
        for i, widget in enumerate(sections):
            key = widget.property("sectionKey") or f"section_{i}"
            by_key[str(key)] = widget

        order = self._load_options_section_order(list(by_key.keys()))
        for key in order:
            widget = by_key.pop(key, None)
            if widget is None:
                continue
            container.add_section(key, widget)
        # A group box this saved order predates (or one whose key changed)
        # still appears, appended at the end rather than silently dropped.
        for key, widget in by_key.items():
            container.add_section(key, widget)

        self._options_sections_container = container
        return container

    def _load_options_section_order(self, available_keys: list) -> list:
        stored = self._options_settings.value("adjustment/optionsSectionOrder", "")
        order = [k for k in str(stored).split("|") if k]
        order = [k for k in order if k in available_keys]
        order += [k for k in available_keys if k not in order]
        return order

    def _save_options_section_order(self, container: "_OptionsSectionsContainer"):
        self._options_settings.setValue(
            "adjustment/optionsSectionOrder", "|".join(container.order())
        )

    def _sync_options_list_heights(self):
        """Ask the Options scroll area to recompute its content size after a
        section's collapse state changes. With the QVBoxLayout-based
        _OptionsSectionsContainer (2026-09-19 map.py port) the container's
        own sizeHint tracks its visible children automatically -- unlike
        the previous QListWidget, whose per-item sizeHint had to be
        refreshed by hand on every collapse/expand -- so this just nudges
        layout invalidation through to the scroll area."""
        container = getattr(self, "_options_sections_container", None)
        if container is not None:
            container.updateGeometry()
        inner = getattr(self, "_options_inner", None)
        if inner is not None:
            inner.updateGeometry()

    def _init_paint_controls(self, ui_layout):
        """Region picker, paint target, view sliders, and brush/edit controls."""
        region_group = QFrame(self)
        region_layout = QVBoxLayout()
        # Tight spacing/margins (2026-09-19 user request: minimize the row-to-row
        # gap inside an expanded section) -- Qt's per-style default QVBoxLayout
        # spacing/margins are noticeably looser than this.
        region_layout.setSpacing(4)
        region_layout.setContentsMargins(6, 4, 6, 6)

        # Each field is a label+control row (2026-09-19 user request: was
        # label-above-field, stacked vertically -- now one horizontal row
        # per field). A fixed label width keeps Search/Tier/Level/Area's
        # controls left-aligned with each other despite "Search"/"Tier"/
        # "Level"/"Area" being different lengths.
        # 2026-09-19 follow-up: a flat 40px fixed width clipped "Search:"
        # (the longest of the four labels) -- sized from the actual font
        # metrics instead, with a small margin, so every label fits without
        # truncation regardless of font/DPI, and all four still line up
        # since they share the same computed width.
        picker_label_texts = ["Search:", "Tier:", "Level:", "Area:"]
        picker_label_width = (
            max(
                self.fontMetrics().horizontalAdvance(text)
                for text in picker_label_texts
            )
            + 6
        )

        def _picker_row(label_text: str, field: QWidget) -> QHBoxLayout:
            row = QHBoxLayout()
            row.setSpacing(6)
            label = QLabel(label_text, self)
            label.setFixedWidth(picker_label_width)
            row.addWidget(label)
            row.addWidget(field, 1)
            return row

        self.area_search_box.setMinimumWidth(80)
        region_layout.addLayout(_picker_row("Search:", self.area_search_box))
        region_layout.addLayout(_picker_row("Tier:", self.tier_combo))
        region_layout.addLayout(_picker_row("Level:", self.level_combo))
        region_layout.addLayout(_picker_row("Area:", self.area_combo))

        region_layout.addWidget(self.ccf_advanced_toggle)
        self.paint_resolution_warning = QLabel("", self)
        self.paint_resolution_warning.setWordWrap(True)
        self.paint_resolution_warning.setStyleSheet("color: #856404;")
        self.paint_resolution_warning.setVisible(False)
        region_layout.addWidget(self.paint_resolution_warning)
        region_group.setLayout(region_layout)
        self._make_group_collapsible(region_group, title="Region picker", draggable=True)
        ui_layout.addWidget(region_group)

        target_group = QFrame(self)
        target_layout = QVBoxLayout()
        # Tight spacing/margins (2026-09-19 user request: minimize the row-to-row
        # gap inside an expanded section) -- Qt's per-style default QVBoxLayout
        # spacing/margins are noticeably looser than this.
        target_layout.setSpacing(4)
        target_layout.setContentsMargins(6, 4, 6, 6)
        target_top = QHBoxLayout()
        target_top.addWidget(self.paint_swatch)
        target_top.addWidget(self.paint_target_name, 1)
        # Keep the selected hierarchy context on the same line as the brain
        # area's name.  It is a concise target summary, not a second control.
        target_top.addWidget(
            self.paint_tier_context,
            0,
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
        )
        target_layout.addLayout(target_top)
        target_layout.addWidget(self.paint_target_fullname)
        target_group.setLayout(target_layout)
        self._make_group_collapsible(target_group, title="Paint target", draggable=True)
        ui_layout.addWidget(target_group)

        view_group = QFrame(self)
        view_layout = QVBoxLayout()
        # Tight spacing/margins (2026-09-19 user request: minimize the row-to-row
        # gap inside an expanded section) -- Qt's per-style default QVBoxLayout
        # spacing/margins are noticeably looser than this.
        view_layout.setSpacing(4)
        view_layout.setContentsMargins(6, 4, 6, 6)
        opacity_row = QHBoxLayout()
        opacity_row.addWidget(self.opacity_label)
        opacity_row.addWidget(self.opacity_slider, 1)
        view_layout.addLayout(opacity_row)
        zoom_row = QHBoxLayout()
        zoom_row.addWidget(self.zoom_label)
        zoom_row.addWidget(self.zoom_slider, 1)
        view_layout.addLayout(zoom_row)
        # LUT (black/white/gamma) folds independently of Opacity/Zoom above
        # it (2026-09-19 user request) -- a plain QWidget sub-container
        # rather than a nested QGroupBox, collapsed with the same
        # _make_group_collapsible() header pattern via its title= override.
        lut_container = QWidget(self)
        lut_layout = QVBoxLayout(lut_container)
        lut_layout.setContentsMargins(0, 0, 0, 0)
        lut_layout.setSpacing(4)
        lut_black_row = QHBoxLayout()
        lut_black_row.addWidget(self.lut_black_label)
        lut_black_row.addWidget(self.lut_black_slider, 1)
        lut_layout.addLayout(lut_black_row)
        lut_white_row = QHBoxLayout()
        lut_white_row.addWidget(self.lut_white_label)
        lut_white_row.addWidget(self.lut_white_slider, 1)
        lut_layout.addLayout(lut_white_row)
        lut_gamma_row = QHBoxLayout()
        lut_gamma_row.addWidget(self.lut_gamma_label)
        lut_gamma_row.addWidget(self.lut_gamma_slider, 1)
        lut_layout.addLayout(lut_gamma_row)
        lut_button_row = QHBoxLayout()
        lut_button_row.addWidget(self.lut_auto_button)
        lut_button_row.addWidget(self.lut_reset_button)
        lut_layout.addLayout(lut_button_row)
        self._make_group_collapsible(lut_container, title="LUT")
        view_layout.addWidget(lut_container)
        view_group.setLayout(view_layout)
        self._make_group_collapsible(view_group, title="View", draggable=True)
        ui_layout.addWidget(view_group)

        brush_group = QFrame(self)
        brush_layout = QVBoxLayout()
        # Tight spacing/margins (2026-09-19 user request: minimize the row-to-row
        # gap inside an expanded section) -- Qt's per-style default QVBoxLayout
        # spacing/margins are noticeably looser than this.
        brush_layout.setSpacing(4)
        brush_layout.setContentsMargins(6, 4, 6, 6)
        brush_state_row = QHBoxLayout()
        brush_state_row.addWidget(QLabel("Brush editing:", self))
        brush_state_row.addWidget(self.paint_adjust_badge)
        self.ring_use_region_check = QCheckBox("Use region color", self)
        self.ring_use_region_check.setChecked(self.brush_cursor_use_region)
        self.ring_use_region_check.setToolTip(
            "Ring uses the selected region's color instead of the custom color"
        )
        self.ring_use_region_check.toggled.connect(
            self._on_brush_ring_use_region_toggled
        )
        brush_state_row.addWidget(self.ring_use_region_check)
        brush_state_row.addStretch()
        brush_layout.addLayout(brush_state_row)
        brush_row = QHBoxLayout()
        brush_row.addWidget(self.brush_label)
        brush_row.addWidget(self.brush_slider, 1)
        brush_layout.addLayout(brush_row)

        ring_row = QHBoxLayout()
        ring_row.addWidget(QLabel("Ring color:", self))
        self.ring_color_button = QPushButton(self)
        self.ring_color_button.setFixedWidth(48)
        self.ring_color_button.setToolTip("Brush cursor ring color")
        self.ring_color_button.clicked.connect(self._pick_brush_ring_color)
        ring_row.addWidget(self.ring_color_button)
        ring_row.addWidget(QLabel("Thickness:", self))
        self.ring_width_spin = QSpinBox(self)
        self.ring_width_spin.setRange(1, 12)
        self.ring_width_spin.setValue(self.brush_cursor_width)
        self.ring_width_spin.setToolTip("Brush cursor ring line width (px)")
        self.ring_width_spin.valueChanged.connect(self._on_brush_ring_width_changed)
        ring_row.addWidget(self.ring_width_spin)
        ring_row.addStretch()
        brush_layout.addLayout(ring_row)

        self.ring_color_button.setEnabled(not self.brush_cursor_use_region)
        self._update_ring_color_swatch()

        btn_row = QHBoxLayout()
        for button in (self.refresh_button, self.undo_button, self.save_button):
            button.setSizePolicy(
                QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
            )
            btn_row.addWidget(button, 1)
        brush_layout.addLayout(btn_row)
        brush_group.setLayout(brush_layout)
        # Toggle Overlay lives back in the top header toolbar (2026-09-19
        # user request -- reverting the previous move to sit beside this
        # section's title: it also left this title no longer flush-left
        # with the other four sections', since the header_extra wrapper
        # put the title QToolButton inside a QHBoxLayout sharing the row
        # instead of the plain layout.insertWidget(0, header) every other
        # section still uses). See header_toolbar construction above for
        # self.overlay_toggle's creation.
        self._make_group_collapsible(
            brush_group,
            title="Brush",
            draggable=True,
            key="Brush & edits",
        )
        ui_layout.addWidget(brush_group)

        self._paint_controls_group = region_group

    def _init_parcellation_controls(self, ui_layout):
        """Parcellation level controls (separate from paint-brush hierarchy)."""
        group = QFrame(self)
        layout = QVBoxLayout()
        # Tight spacing/margins (2026-09-19 user request: minimize the row-to-row
        # gap inside an expanded section) -- Qt's per-style default QVBoxLayout
        # spacing/margins are noticeably looser than this.
        layout.setSpacing(4)
        layout.setContentsMargins(6, 4, 6, 6)

        self.parcel_status_label = QLabel("", self)
        self.parcel_status_label.setWordWrap(True)
        layout.addWidget(self.parcel_status_label)

        self.parcel_tier_combo = QComboBox(self)
        self.parcel_tier_combo.currentIndexChanged.connect(self._on_parcel_tier_changed)
        self.parcel_level_combo = QComboBox(self)
        self.parcel_level_combo.currentIndexChanged.connect(
            self._on_parcel_level_changed
        )
        self.parcel_level_combo.setEnabled(False)
        target_row = QHBoxLayout()
        target_row.addWidget(QLabel("Roll up to:", self))
        target_row.addWidget(self.parcel_tier_combo, 1)
        layout.addLayout(target_row)

        # Match Region picker's Label → Combo layout when using CCF levels.
        level_row = QHBoxLayout()
        self.parcel_level_label = QLabel("Level:", self)
        level_row.addWidget(self.parcel_level_label)
        level_row.addWidget(self.parcel_level_combo, 1)
        layout.addLayout(level_row)

        parcel_toggle_row = QHBoxLayout()
        self.parcel_ccf_advanced_toggle = QCheckBox(
            "Advanced CCFv3", self
        )
        self.parcel_ccf_advanced_toggle.toggled.connect(
            self._on_parcel_ccf_advanced_toggled
        )
        self.parcel_preview_toggle = QCheckBox("Preview borders", self)
        self.parcel_preview_toggle.setChecked(False)
        self.parcel_preview_toggle.toggled.connect(self._on_parcel_preview_toggled)
        parcel_toggle_row.addWidget(self.parcel_ccf_advanced_toggle)
        parcel_toggle_row.addWidget(self.parcel_preview_toggle)
        parcel_toggle_row.addStretch()
        layout.addLayout(parcel_toggle_row)

        self.parcel_applied_label = QLabel("", self)
        self.parcel_applied_label.setWordWrap(True)
        layout.addWidget(self.parcel_applied_label)

        self.parcel_backup_label = QLabel("", self)
        layout.addWidget(self.parcel_backup_label)

        btn_row = QHBoxLayout()
        self.parcel_apply_button = QPushButton("Apply…", self)
        self.parcel_apply_button.clicked.connect(self.apply_parcellation)
        self.parcel_restore_button = QPushButton("Restore…", self)
        self.parcel_restore_button.clicked.connect(self.restore_fine_parcellation)
        self.parcel_apply_all_button = QPushButton("Apply all…", self)
        self.parcel_apply_all_button.setToolTip(
            "Apply the current parcellation target to every section and write the "
            "annotations to disk."
        )
        self.parcel_apply_all_button.clicked.connect(self.apply_parcellation_all)
        for button in (
            self.parcel_apply_button,
            self.parcel_restore_button,
            self.parcel_apply_all_button,
        ):
            button.setSizePolicy(
                QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
            )
            btn_row.addWidget(button, 1)
        layout.addLayout(btn_row)

        self.parcel_quick_areas_button = QPushButton("Layers → areas", self)
        self.parcel_quick_areas_button.setToolTip(
            "Roll cortical layers up to functional areas on this section only."
        )
        self.parcel_quick_areas_button.clicked.connect(self.convert_to_parents)
        layout.addWidget(self.parcel_quick_areas_button)

        exclude_row = QHBoxLayout()
        self.parcel_exclude_button = QPushButton("Exclude area", self)
        self.parcel_exclude_button.setToolTip(
            "Add the paint-brush Area selection to the exclude list for parcellation."
        )
        self.parcel_exclude_button.clicked.connect(self._add_parcel_exclude_area)
        self.parcel_clear_exclude_button = QPushButton("Clear", self)
        self.parcel_clear_exclude_button.clicked.connect(self._clear_parcel_excludes)
        for button in (
            self.parcel_exclude_button,
            self.parcel_clear_exclude_button,
        ):
            button.setSizePolicy(
                QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
            )
            exclude_row.addWidget(button, 1)
        layout.addLayout(exclude_row)

        self.parcel_exclude_list = QListWidget(self)
        self.parcel_exclude_list.setMaximumHeight(80)
        layout.addWidget(self.parcel_exclude_list)

        group.setLayout(layout)
        self._make_group_collapsible(group, title="Parcellation", draggable=True)
        ui_layout.addWidget(group)
        self._parcellation_group = group
        # The spacer belongs after the final group.  Placing it before
        # Parcellation pins that group to the dock bottom, making its header
        # move downward when its body is collapsed.
        ui_layout.addStretch()

        if not self.catalog:
            self.parcel_tier_combo.setEnabled(False)
            self.parcel_level_combo.setEnabled(False)
            self.parcel_ccf_advanced_toggle.setEnabled(False)
            self.parcel_preview_toggle.setEnabled(False)
            self.parcel_apply_button.setEnabled(False)
            self.parcel_apply_all_button.setEnabled(False)
            self.parcel_restore_button.setEnabled(False)
            self._update_parcellation_labels()
            return

        self.parcel_tier_combo.blockSignals(True)
        self.parcel_tier_combo.clear()
        self.parcel_tier_combo.addItem("Full detail", FULL_DETAIL_TIER)
        self.parcel_tier_combo.setItemData(
            0,
            "Keep annotation IDs as drawn (no rollup).",
            Qt.ItemDataRole.ToolTipRole,
        )
        tiers = list_tiers(self.catalog)
        default_tier_index = 0
        for i, tier in enumerate(tiers):
            self.parcel_tier_combo.addItem(tier["label"], tier["id"])
            tip = tier.get("description") or ""
            if tier["id"] == "layers":
                tip = tip or "Laminar resolution for paint / rollup."
            self.parcel_tier_combo.setItemData(
                i + 1, tip, Qt.ItemDataRole.ToolTipRole
            )
            if tier["id"] == self.parcel_tier_id:
                default_tier_index = i + 1
        self.parcel_tier_combo.setCurrentIndex(default_tier_index)
        self.parcel_tier_combo.blockSignals(False)
        self.parcel_tier_id = self.parcel_tier_combo.currentData() or "areas"

        self.parcel_level_combo.blockSignals(True)
        self.parcel_level_combo.clear()
        levels = list_ccf_levels(self.catalog)
        default_level_index = 0
        for i, info in enumerate(levels):
            # Keep the compact level label and acronym tooltip consistent with
            # the Region Picker.  The former local helper was removed when the
            # shared catalog formatter was introduced.
            label, tooltip = compact_ccf_level_label_and_tooltip(info)
            self.parcel_level_combo.addItem(label, info["level"])
            self.parcel_level_combo.setItemData(
                i, tooltip, Qt.ItemDataRole.ToolTipRole
            )
            if info["level"] == 6:
                default_level_index = i
        self.parcel_level_combo.setCurrentIndex(default_level_index)
        self.parcel_level_combo.blockSignals(False)
        self._show_current_combo_item_tooltip(self.parcel_level_combo)

        self._sync_parcellation_ui_from_metadata()

    def _parcel_excluded_region_ids(self) -> list[int]:
        return list(self.parcel_excluded_ids)

    def _add_parcel_exclude_area(self):
        if self.selected_region_id is None:
            return
        rid = int(self.selected_region_id)
        if rid not in self.parcel_excluded_ids:
            self.parcel_excluded_ids.append(rid)
            node = get_region(rid, self.catalog) if self.catalog else None
            label = self._region_display_text(node) if node else str(rid)
            self.parcel_exclude_list.addItem(label)

    def _reload_parcel_excludes_from_metadata(self, entry: dict | None = None):
        """Reload exclude list for the current slice from parcellation metadata."""
        self.parcel_excluded_ids = []
        self.parcel_exclude_list.clear()
        if entry is None:
            entry = get_slice_parcellation(
                self.annotation_dir, self._current_slice_id()
            )
        excluded = entry.get("excluded_region_ids") if entry else None
        if not excluded:
            return
        for rid in excluded:
            rid_int = int(rid)
            self.parcel_excluded_ids.append(rid_int)
            node = get_region(rid_int, self.catalog) if self.catalog else None
            label = self._region_display_text(node) if node else str(rid_int)
            self.parcel_exclude_list.addItem(label)

    def _clear_parcel_excludes(self):
        """Clear exclude list for the current slice (does not persist until apply)."""
        self.parcel_excluded_ids = []
        self.parcel_exclude_list.clear()

    def _parcel_target(self) -> tuple[str | None, int | None]:
        if not self.catalog:
            return None, None
        if self.parcel_ccf_advanced:
            level = self.parcel_level_combo.currentData()
            return None, int(level) if level is not None else None
        tier_id = self.parcel_tier_combo.currentData()
        if tier_id == FULL_DETAIL_TIER:
            return FULL_DETAIL_TIER, None
        return str(tier_id) if tier_id else None, None

    def _parcellation_baseline(self) -> np.ndarray | None:
        slice_id = self._current_slice_id()
        backup = load_full_backup(self.annotation_dir, slice_id)
        if backup is not None:
            return backup
        return np.asarray(self.current_label, dtype=np.uint32)

    def _update_parcellation_labels(self):
        slice_id = self._current_slice_id()
        n = self.current_index + 1
        m = len(self.pairs)
        unsaved_hint = (
            "\nUnsaved brush edits on disk" if self.was_changed else ""
        )
        self.parcel_status_label.setText(
            f"Section: {slice_id} ({n} / {m}){unsaved_hint}"
        )

        if self.catalog:
            tier_id, st_level = self._parcel_target()
            target = parcellation_target_label(
                self.catalog,
                tier_id=tier_id,
                st_level=st_level,
                ccf_advanced=self.parcel_ccf_advanced,
            )
            entry = get_slice_parcellation(self.annotation_dir, slice_id)
            applied = format_applied_parcellation(entry, self.catalog)
            # Keep the applied timestamp from widening the current-level row.
            applied = applied.replace(" (applied ", "\n(applied ", 1)
            self.parcel_applied_label.setText(f"Current level: {applied}")
            self.parcel_apply_button.setToolTip(
                f"Apply {target} parcellation to this section only."
            )
        else:
            self.parcel_applied_label.setText("Current level: (catalog unavailable)")

        if has_full_backup(self.annotation_dir, slice_id):
            self.parcel_backup_label.setText("Fine backup: saved")
            self.parcel_restore_button.setEnabled(True)
        else:
            self.parcel_backup_label.setText("Fine backup: not saved")
            self.parcel_restore_button.setEnabled(False)

    def _sync_parcellation_ui_from_metadata(self):
        if not self.catalog:
            self._update_parcellation_labels()
            return

        entry = get_slice_parcellation(self.annotation_dir, self._current_slice_id())
        self.parcel_tier_combo.blockSignals(True)
        self.parcel_ccf_advanced_toggle.blockSignals(True)

        if entry and entry.get("st_level") is not None and entry.get("tier_id") is None:
            self.parcel_ccf_advanced = True
            self.parcel_ccf_advanced_toggle.setChecked(True)
            self.parcel_tier_combo.setEnabled(False)
            self.parcel_level_combo.setEnabled(True)
            level = int(entry["st_level"])
            for i in range(self.parcel_level_combo.count()):
                if self.parcel_level_combo.itemData(i) == level:
                    self.parcel_level_combo.setCurrentIndex(i)
                    break
        elif entry and entry.get("tier_id"):
            self.parcel_ccf_advanced = False
            self.parcel_ccf_advanced_toggle.setChecked(False)
            self.parcel_tier_combo.setEnabled(True)
            self.parcel_level_combo.setEnabled(False)
            tier_id = entry["tier_id"]
            for i in range(self.parcel_tier_combo.count()):
                if self.parcel_tier_combo.itemData(i) == tier_id:
                    self.parcel_tier_combo.setCurrentIndex(i)
                    break
            self.parcel_tier_id = tier_id
        else:
            self.parcel_ccf_advanced = False
            self.parcel_ccf_advanced_toggle.setChecked(False)
            self.parcel_tier_combo.setEnabled(True)
            self.parcel_level_combo.setEnabled(False)
            self.parcel_tier_combo.setCurrentIndex(0)

        self.parcel_tier_combo.blockSignals(False)
        self.parcel_ccf_advanced_toggle.blockSignals(False)
        self._show_current_combo_item_tooltip(self.parcel_level_combo)
        # Preserve the user's Preview borders setting while changing sections.
        # It starts disabled for a newly opened viewer.
        self.parcel_preview = self.parcel_preview_toggle.isChecked()
        self.parcel_preview_toggle.blockSignals(True)
        self.parcel_preview_toggle.setChecked(self.parcel_preview)
        self.parcel_preview_toggle.blockSignals(False)
        self.parcel_preview_array = None
        if self.parcel_preview:
            self._rebuild_parcel_preview()
        self._reload_parcel_excludes_from_metadata(entry)
        self._update_parcellation_labels()

    def _on_parcel_tier_changed(self, _index: int):
        tier_id = self.parcel_tier_combo.currentData()
        if tier_id:
            self.parcel_tier_id = tier_id
        self._refresh_parcel_preview()

    def _on_parcel_level_changed(self, _index: int):
        self._show_current_combo_item_tooltip(self.parcel_level_combo)
        self._refresh_parcel_preview()

    def _on_parcel_ccf_advanced_toggled(self, checked: bool):
        self.parcel_ccf_advanced = bool(checked)
        self.parcel_tier_combo.setEnabled(not self.parcel_ccf_advanced)
        self.parcel_level_combo.setEnabled(self.parcel_ccf_advanced)
        self._refresh_parcel_preview()

    def _refresh_parcel_preview(self):
        self._update_parcellation_labels()
        if self.parcel_preview:
            self._rebuild_parcel_preview()
            self.show_image_with_overlay()

    def _rebuild_parcel_preview(self):
        if not self.catalog:
            self.parcel_preview_array = None
            return
        baseline = self._parcellation_baseline()
        if baseline is None:
            self.parcel_preview_array = None
            return
        tier_id, st_level = self._parcel_target()
        result = relabel_to_target(
            baseline,
            self.catalog,
            tier_id=tier_id,
            st_level=st_level,
            structure_map=self.structure_map,
        )
        label = result.label_array
        if self.parcel_excluded_ids:
            ex_set = expand_excluded_ids(
                self.structure_map, self.parcel_excluded_ids
            )
            label, _excluded = apply_exclusion(label, ex_set)
        self.parcel_preview_array = label

    def _on_parcel_preview_toggled(self, checked: bool):
        self.parcel_preview = bool(checked)
        if self.parcel_preview:
            self._rebuild_parcel_preview()
        else:
            self.parcel_preview_array = None
        self.show_image_with_overlay()

    def _label_for_display(self):
        if self.parcel_preview and self.parcel_preview_array is not None:
            return self.parcel_preview_array
        return self.current_label

    def _confirm_parcellation_apply(self, slice_id: str, target_label: str) -> bool:
        dialog = QMessageBox(self)
        dialog.setIcon(QMessageBox.Icon.Warning)
        dialog.setWindowTitle("Apply parcellation")
        dialog.setText(f"Apply parcellation to {slice_id}?")
        unsaved = (
            "\n\nYou have unsaved brush strokes on this section."
            if self.was_changed
            else ""
        )
        dialog.setInformativeText(
            f"This will replace the annotation borders on this section only at "
            f"{target_label}. Any manual brush adjustments on this section will be "
            f"reverted (unsaved strokes are lost; saved strokes are overwritten when "
            f"you Save).{unsaved}\n\nOther sections are not changed."
        )
        dialog.setStandardButtons(
            QMessageBox.StandardButton.Apply | QMessageBox.StandardButton.Cancel
        )
        dialog.setDefaultButton(QMessageBox.StandardButton.Cancel)
        return dialog.exec() == QMessageBox.StandardButton.Apply

    def _push_relabel_undo(self, before_array: np.ndarray, after_array: np.ndarray):
        changed = before_array != after_array
        if not np.any(changed):
            return
        ys, xs = np.where(changed)
        if len(self.deltas) <= self.current_delta:
            self.deltas.append(set())
            self.originals.append({})
        points: set[tuple[int, int]] = set()
        originals: dict[tuple[int, int], int] = {}
        for y, x in zip(ys, xs):
            p = (int(x), int(y))
            points.add(p)
            originals[p] = int(before_array[y, x])
        self.deltas[self.current_delta] = points
        self.originals[self.current_delta] = originals
        self.current_delta += 1

    def _apply_parcellation_from_baseline(
        self,
        *,
        tier_id: str | None,
        st_level: int | None,
        update_metadata: bool,
        slice_id: str | None = None,
        confirm: bool = True,
        write_disk: bool = False,
    ) -> bool:
        if not self.catalog:
            return False
        sid = slice_id or self._current_slice_id()
        target_label = parcellation_target_label(
            self.catalog,
            tier_id=tier_id,
            st_level=st_level,
            ccf_advanced=self.parcel_ccf_advanced,
        )
        if confirm and not self._confirm_parcellation_apply(sid, target_label):
            return False

        before = np.asarray(self.current_label, dtype=np.uint32) if sid == self._current_slice_id() else None
        if before is None and write_disk:
            pkl_path = self.annotation_dir / f"Annotation_{sid}.pkl"
            if pkl_path.is_file():
                with pkl_path.open("rb") as f:
                    before = np.asarray(pickle.load(f), dtype=np.uint32)

        result = apply_parcellation_to_slice(
            self.annotation_dir,
            sid,
            tier_id=tier_id,
            st_level=st_level,
            excluded_region_ids=self._parcel_excluded_region_ids() or None,
            structure_map=self.structure_map,
            catalog=self.catalog,
            write_disk=write_disk,
        )
        if not result.ok:
            self.status_bar.showMessage(f"{sid}: failed — {result.error}")
            return False

        if sid == self._current_slice_id() and result.label_array is not None:
            if before is not None:
                self._push_relabel_undo(before, result.label_array)
            self.selected_region_id = None
            self.selected_region_name = "None"
            self.current_label = result.label_array
            self.parcel_preview = False
            self.parcel_preview_toggle.blockSignals(True)
            self.parcel_preview_toggle.setChecked(False)
            self.parcel_preview_toggle.blockSignals(False)
            self.parcel_preview_array = None
            self.was_changed = True
            self.show_image_with_overlay()

        if update_metadata and write_disk:
            pass  # metadata written by apply_parcellation_to_slice

        summary = (
            f"{sid}: relabeled {result.pixels_changed:,} px; "
            f"excluded {result.excluded_pixels:,} px; "
            f"{len(result.unknown_ids)} unmapped ids"
        )
        self.status_bar.showMessage(summary)
        self._update_parcellation_labels()
        return True

    def apply_parcellation(self):
        tier_id, st_level = self._parcel_target()
        if tier_id == FULL_DETAIL_TIER and not self._parcel_excluded_region_ids():
            QMessageBox.information(
                self,
                "Full detail",
                "Choose a coarser parcellation target, add excludes, or use Restore fine.",
            )
            return
        self._apply_parcellation_from_baseline(
            tier_id=tier_id,
            st_level=st_level,
            update_metadata=True,
            write_disk=False,
        )

    def apply_parcellation_all(self):
        """Apply the current parcellation target to every section (writes disk)."""
        if not self.catalog:
            return
        tier_id, st_level = self._parcel_target()
        if tier_id == FULL_DETAIL_TIER and not self._parcel_excluded_region_ids():
            QMessageBox.information(
                self,
                "Full detail",
                "Choose a coarser parcellation target, add excludes, or use Restore fine.",
            )
            return
        target_label = parcellation_target_label(
            self.catalog,
            tier_id=tier_id,
            st_level=st_level,
            ccf_advanced=self.parcel_ccf_advanced,
        )
        n = len(self.pairs)
        reply = QMessageBox.question(
            self,
            "Apply to all sections",
            f"Apply parcellation to {target_label} on ALL {n} section(s)?\n\n"
            "This rewrites each section's annotation on disk. The current section's "
            "in-memory state (including unsaved strokes) is used as its baseline.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        ok = 0
        failed = 0
        cancelled = False
        progress = QProgressDialog(
            f"Applying parcellation → {target_label}…", "Cancel", 0, n, self
        )
        progress.setWindowTitle("Apply to all sections")
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.setMinimumDuration(0)
        progress.setValue(0)
        self.parcel_apply_all_button.setEnabled(False)
        try:
            for i, (_img, _anno, sid) in enumerate(self.pairs):
                if progress.wasCanceled():
                    cancelled = True
                    break
                progress.setLabelText(
                    f"Target: {target_label}\n{i + 1}/{n}: {sid}"
                )
                progress.setValue(i)
                QApplication.processEvents()
                try:
                    done = self._apply_parcellation_from_baseline(
                        tier_id=tier_id,
                        st_level=st_level,
                        update_metadata=True,
                        slice_id=sid,
                        confirm=False,
                        write_disk=True,
                    )
                    if done:
                        ok += 1
                    else:
                        failed += 1
                except Exception as exc:
                    failed += 1
                    print(f"LOG: parcellation_all {sid} error {exc}", flush=True)
            progress.setValue(n)
        finally:
            progress.close()
            self.parcel_apply_all_button.setEnabled(True)
        msg = f"Parcellation applied: {ok} ok, {failed} failed"
        if cancelled:
            msg += " (cancelled — already-processed sections kept)"
        self.status_bar.showMessage(msg + ".")

    def restore_fine_parcellation(self):
        slice_id = self._current_slice_id()
        backup = load_full_backup(self.annotation_dir, slice_id)
        if backup is None:
            QMessageBox.warning(
                self,
                "No backup",
                f"No full-detail backup exists for {slice_id}.",
            )
            return

        dialog = QMessageBox(self)
        dialog.setIcon(QMessageBox.Icon.Warning)
        dialog.setText(f"Restore full detail for {slice_id}?")
        dialog.setInformativeText(
            "This replaces the current annotation on this section only with the "
            "saved full-detail backup. Manual brush adjustments on this section "
            "will be reverted. Other sections are not changed."
        )
        dialog.setStandardButtons(
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel
        )
        dialog.setDefaultButton(QMessageBox.StandardButton.Cancel)
        yes_btn = dialog.button(QMessageBox.StandardButton.Yes)
        if yes_btn is not None:
            yes_btn.setText("Restore")
        if dialog.exec() != QMessageBox.StandardButton.Yes:
            return

        before = np.asarray(self.current_label, dtype=np.uint32)
        result = restore_slice_from_backup(
            self.annotation_dir, slice_id, write_disk=True
        )
        if not result.ok:
            return
        backup_arr = load_full_backup(self.annotation_dir, slice_id)
        if backup_arr is None:
            return
        self._push_relabel_undo(before, backup_arr)
        self.current_label = np.asarray(backup_arr, dtype=np.uint32)
        self.was_changed = True
        clear_slice_parcellation(self.annotation_dir, slice_id)
        self.parcel_preview = False
        self.parcel_preview_toggle.blockSignals(True)
        self.parcel_preview_toggle.setChecked(False)
        self.parcel_preview_toggle.blockSignals(False)
        self.parcel_preview_array = None
        self._sync_parcellation_ui_from_metadata()
        self.show_image_with_overlay()

    def _compute_lut(self) -> np.ndarray:
        """256-entry uint8 lookup table from the current black/white/gamma."""
        black = self.lut_black
        white = self.lut_white
        if white <= black:
            white = black + 1
        gamma = max(self.lut_gamma, 1) / 100.0
        ramp = np.clip(
            (np.arange(256, dtype=np.float32) - black) / (white - black), 0.0, 1.0
        )
        if gamma != 1.0:
            ramp = np.power(ramp, 1.0 / gamma)
        return np.clip(ramp * 255.0, 0, 255).astype(np.uint8)

    def _update_lut_labels(self):
        self.lut_black_label.setText(f"Black {self.lut_black}")
        self.lut_white_label.setText(f"White {self.lut_white}")
        self.lut_gamma_label.setText(f"Gamma {self.lut_gamma / 100.0:.2f}")

    def _apply_lut_to_display(self):
        """Re-render the background image from the cached raw array.

        Applying the LUT (a 256-entry numpy fancy-index, <10ms even on a
        multi-megapixel preview) and rebuilding the pixmap from the already
        in-memory array means every slider move is free of disk I/O -- no
        re-read of the source PNG, matching how opacity/zoom/brush already
        update without touching disk.
        """
        if self._active_channel_array is None:
            return
        lut = self._compute_lut()
        adjusted = lut[self._active_channel_array]
        pixmap = QPixmap.fromImage(numpy_array_to_qimage(adjusted))
        pixmap = pixmap.scaled(
            self.current_label.shape[1],
            self.current_label.shape[0],
            Qt.AspectRatioMode.KeepAspectRatio,
        )
        self.img_pixmap = pixmap
        # Preserve the viewport's exact scroll position across this
        # re-render (2026-09-19 user report, 4th attempt: LUT slider, Seam
        # Correction toggle, Auto, and Reset all still nudged the slice --
        # vertically for the slider/toggle, both vertically and
        # horizontally for Auto/Reset -- despite three earlier fixes aimed
        # at scene.setSceneRect() itself (a no-op guard, cached pan
        # margins, and skipping the zoom transform reset).
        #
        # Root cause: this method's own capture-then-restore of a *scene*
        # coordinate (via _viewport_center_scene_pos()/_center_linked_views(),
        # i.e. QGraphicsView.centerOn()) was not actually the last word on
        # the viewport's position. show_image_with_overlay(), called in
        # between, does its own independent capture-then-restore around
        # rebuilding the annotation pixmap (_set_anno_pixmap() ->
        # _lock_scene_rect_to_pixmap(anno_scene, ...)) and ends by calling
        # _apply_zoom(), which re-derives a scrollbar position from
        # QGraphicsView.centerOn() again. Each centerOn() call maps a
        # scene-space float back to an integer scrollbar value through the
        # view's current transform and scrollbar range; if either of those
        # shifted even slightly between the two nested capture/restore
        # passes (a real, if small, annotation-scene rect change happens on
        # every re-render, unlike the background image scene, which mostly
        # doesn't), the same target scene point can round to a different
        # achievable pixel each time -- a redundant final centerOn() at the
        # very end does not undo that, since it recomputes the same
        # rounding from the same (by-then-already-off) state.
        #
        # Saving and restoring the *scrollbar pixel values themselves*
        # (rather than a scene coordinate that has to be re-mapped through
        # a transform and range that may have shifted) sidesteps that
        # rounding entirely: as long as the saved value is still inside the
        # scrollbar's range after the re-render (true here -- neither the
        # zoom nor the pixmap's on-screen size changes from this method),
        # restoring it reproduces the exact former pixel position, with no
        # coordinate round-trip at all.
        scrollbar_state = [
            (
                view.horizontalScrollBar().value(),
                view.verticalScrollBar().value(),
            )
            for view in (self.img_view, self.anno_view)
        ]
        self._set_img_pixmap(pixmap)
        self.show_image_with_overlay()
        self._syncing_scroll = True
        try:
            for view, (h_value, v_value) in zip(
                (self.img_view, self.anno_view), scrollbar_state
            ):
                view.horizontalScrollBar().setValue(h_value)
                view.verticalScrollBar().setValue(v_value)
        finally:
            self._syncing_scroll = False
        self._refresh_compare_if_active()

    def _on_lut_black_changed(self, value: int):
        if value >= self.lut_white_slider.value():
            value = self.lut_white_slider.value() - 1
            self.lut_black_slider.blockSignals(True)
            self.lut_black_slider.setValue(value)
            self.lut_black_slider.blockSignals(False)
        self.lut_black = value
        self._update_lut_labels()
        self._apply_lut_to_display()

    def _on_lut_white_changed(self, value: int):
        if value <= self.lut_black_slider.value():
            value = self.lut_black_slider.value() + 1
            self.lut_white_slider.blockSignals(True)
            self.lut_white_slider.setValue(value)
            self.lut_white_slider.blockSignals(False)
        self.lut_white = value
        self._update_lut_labels()
        self._apply_lut_to_display()

    def _on_lut_gamma_changed(self, value: int):
        self.lut_gamma = value
        self._update_lut_labels()
        self._apply_lut_to_display()

    def _auto_lut(self):
        """Set Black/White/Gamma from this channel's own pixel statistics.

        2026-09-18 first version stretched black/white to the tissue's
        1st/99th percentile (restricted to tissue pixels, img > 8 --
        seam_correct.py's own tissue_threshold=8 -- so slide background
        doesn't dominate the percentile) and then solved for a gamma that
        maps the tissue's median brightness to mid-gray after that
        stretch. 2026-09-19 user report: on their slice this produced
        black 24 / white 234 / gamma 0.86, which they judged as *less*
        contrast than what they set by hand -- black 0, white 255 (i.e.
        no stretch at all), gamma 0.37.

        That comparison says the percentile stretch itself was the wrong
        move for this kind of image, not just mistuned: narrowing
        black/white away from the full 0-255 range compresses the
        histogram's normalized spread that the gamma curve then has to
        work with, so solving for the same target (median -> mid-gray)
        against a narrower span needs a *milder* gamma than solving
        against the full range would -- 0.86 is much closer to 1
        (identity) than the user's preferred 0.37. Leaving black/white at
        the identity 0/255 the user actually chose removes that
        confound entirely: Auto now only ever adjusts gamma, matching
        the black=0/white=255 half of their stated preference exactly,
        and picks the gamma value that would have produced their
        preferred visual effect on a typical slice.

        Gamma: _compute_lut()'s convention is
        ramp = ((value-black)/(white-black)) ** (1/gamma); with
        black=0/white=255 that is ramp = (value/255) ** (1/gamma).
        Solving ramp(median) = 0.5 (map the tissue's own median
        brightness to mid-gray) gives gamma = ln(x) / ln(0.5) where
        x = median/255. A median above 128 (x > 0.5, a brighter-skewed
        tissue histogram) needs gamma < 1 to pull it back down to
        mid-gray -- consistent with the user's 0.37 preference -- and a
        median below 128 needs gamma > 1 to lift it. Clamped to the
        Gamma slider's own range (0.10-3.00)."""
        if self._active_channel_array is None:
            return
        array = self._active_channel_array
        tissue = array > 8
        sample = array[tissue] if np.any(tissue) else array
        median = float(np.median(sample))
        x = median / 255.0
        x = min(max(x, 1e-3), 1.0 - 1e-3)
        gamma = float(np.log(x) / np.log(0.5))
        gamma = min(max(gamma, 0.10), 3.00)
        gamma_value = int(round(gamma * 100))
        self.lut_black = 0
        self.lut_white = 255
        self.lut_gamma = gamma_value
        for slider, value in (
            (self.lut_black_slider, 0),
            (self.lut_white_slider, 255),
            (self.lut_gamma_slider, gamma_value),
        ):
            slider.blockSignals(True)
            slider.setValue(value)
            slider.blockSignals(False)
        self._update_lut_labels()
        self._apply_lut_to_display()

    def _reset_lut(self):
        self.lut_black = 0
        self.lut_white = 255
        self.lut_gamma = 100
        for slider, value in (
            (self.lut_black_slider, 0),
            (self.lut_white_slider, 255),
            (self.lut_gamma_slider, 100),
        ):
            slider.blockSignals(True)
            slider.setValue(value)
            slider.blockSignals(False)
        self._update_lut_labels()
        self._apply_lut_to_display()

    def switch_channel(self, path, display_name, *, display_path=None):
        """Load a low-res background image and refresh the annotation overlay."""
        path = Path(path)
        self.active_channel_path = path
        # A session-cached live seam image has a temporary hashed filename.
        # Retain the original channel path for the user-facing Background line.
        self.active_channel_display_path = Path(display_path) if display_path else path
        self.active_channel_name = display_name
        with perf_log.perf_section("adjust.channel.load_image"):
            # Read as a raw grayscale array (not straight to QPixmap) so the
            # brightness/contrast LUT (black/white/gamma, 2026-09-18 user
            # request) can be re-applied on slider moves without re-reading
            # the file from disk. All background channel PNGs written by
            # this pipeline (_previews/*.png, 00_dapi/*.png) are single-
            # channel 8-bit; IMREAD_GRAYSCALE is a no-op format-wise for
            # those and a safe fallback for anything else.
            array = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
        if array is None:
            # Missing/corrupt file: fall back to the pre-LUT behavior (a
            # null QPixmap) rather than raising, matching what a direct
            # QPixmap(str(path)) load on a bad path already did silently.
            self._active_channel_array = None
            self.img_pixmap = QPixmap()
            self._set_img_pixmap(self.img_pixmap)
            self._update_section_labels()
            self.show_image_with_overlay()
            return
        self._active_channel_array = array
        self._apply_lut_to_display()
        self._update_section_labels()

    def _on_compare_adjacent_toggled(self, checked: bool):
        """Compare Adjacent (2026-09-22 user request): temporarily show an
        adjacent slice's DAPI in the Annotation pane, read-only, so the
        user can visually check internal-structure continuity across
        slices without leaving the current slice or touching its
        annotation data at all.
        """
        if checked:
            if not self._enter_compare_mode():
                self.compare_adjacent_toggle.blockSignals(True)
                self.compare_adjacent_toggle.setChecked(False)
                self.compare_adjacent_toggle.blockSignals(False)
            return
        self._exit_compare_mode()

    def _on_compare_offset_changed(self, _value: int):
        """Changing the offset while comparing reloads immediately; while
        not comparing, this only changes what the next toggle-on will
        show."""
        if self._compare_mode_active:
            self._enter_compare_mode()

    def _compare_target_index(self):
        offset = self.compare_offset_spin.value()
        if offset == 0:
            return None
        target = self.current_index + offset
        if target < 0 or target >= len(self.pairs):
            return None
        return target

    def _enter_compare_mode(self) -> bool:
        """(Re)load the selected adjacent slice's DAPI into the Annotation
        pane. Returns False -- leaving any prior compare state untouched --
        when the selected direction has no adjacent slice (first/last
        section); callers must not flip the toggle on in that case.
        """
        offset = self.compare_offset_spin.value()
        target = self._compare_target_index()
        if target is None:
            if offset == 0:
                self.status_bar.showMessage(
                    "Set a nonzero offset to compare against another section."
                )
            else:
                which = "after" if offset > 0 else "before"
                self.status_bar.showMessage(
                    f"No section {abs(offset)} slice(s) {which} the current one."
                )
            if self._compare_mode_active:
                # Compare Adjacent was already on and the user dialed the
                # offset spinbox to a value with no matching slice (e.g.
                # -2 while already comparing at -1 on the first section).
                # Hide the comparison layer instead of silently leaving the
                # previous offset's image on screen (2026-09-22 user
                # request) -- this reveals the real current-slice
                # annotation underneath (2026-09-23: it is a separate
                # item now, always kept current -- see
                # _set_compare_pixmap()), not a blank pane.
                self._hide_compare_pixmap()
            self._compare_adjacent_label_array = None
            self._compare_adjacent_slice_id = None
            self._compare_pixmap_offset = (0, 0)
            self._compare_pixmap_native_size = (0, 0)
            return False

        _, adjacent_anno_path, adjacent_slice_id = self.pairs[target]
        self._compare_adjacent_slice_id = adjacent_slice_id
        channel_sources = lowres_channels_for_slice(
            self.images_dir,
            adjacent_slice_id,
            self.previews_dir,
            self._preview_channel_index,
        )
        if not channel_sources:
            self.status_bar.showMessage(
                f"No preview channels found for {adjacent_slice_id}."
            )
            return False

        # Prefer the channel currently shown in the main DAPI pane so the
        # comparison is apples-to-apples; fall back to DAPI, then whatever
        # is first, the same fallback order rebuild_channel_combo() uses.
        active_name = getattr(self, "active_channel_name", None)
        path = None
        for name, candidate_path in channel_sources:
            if active_name and name == active_name:
                path = candidate_path
                break
        if path is None:
            for name, candidate_path in channel_sources:
                if name in ("DAPI", "DAPI (pipeline)", "Dapi"):
                    path = candidate_path
                    break
        if path is None:
            path = channel_sources[0][1]

        # Mirror Seam Correction onto the comparison image too (2026-09-23
        # user request): previously this always read the adjacent slice's
        # plain preview PNG, so turning Seam Correction on/off for the
        # current slice re-rendered the comparison pane (via
        # _refresh_compare_if_active(), called from switch_channel() ->
        # _apply_lut_to_display()) but always with the same uncorrected
        # source -- the seam fix itself never actually reached this image.
        # Reuses the same live-correction cache as the main pane
        # (_dapi_live_cache/_cached_dapi_live_path), just keyed by the
        # adjacent slice's own id instead of the current one.
        if (
            getattr(self, "seam_channel_toggle", None) is not None
            and self.seam_channel_toggle.isChecked()
        ):
            cached = self._cached_dapi_live_path(adjacent_slice_id, path)
            if cached is not None:
                path = cached
            else:
                live_path = self._compute_seam_live_for_slice(
                    path, adjacent_slice_id
                )
                if live_path is not None:
                    self._dapi_live_cache[
                        self._dapi_live_cache_key(adjacent_slice_id, path)
                    ] = live_path
                    path = live_path
                # On failure, fall through and show the plain preview
                # rather than failing the whole comparison.

        array = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
        if array is None:
            self.status_bar.showMessage(
                f"Could not load DAPI image for {adjacent_slice_id}."
            )
            return False

        # Reuse the main pane's current LUT (black/white/gamma) so the
        # comparison image's contrast matches what's already on screen,
        # instead of showing the adjacent slice at raw/default brightness.
        lut = self._compute_lut()
        adjusted = lut[array]

        # DAPI+annotation composite (2026-09-22 user request): blend in the
        # adjacent slice's own colored label overlay, the same way the
        # main DAPI pane blends its overlay in, instead of showing bare
        # DAPI. Respects the Toggle Overlay checkbox (self.overlay_visible)
        # and the current Opacity slider, matching what the main pane
        # would show for that slice. Falls back to plain DAPI on any
        # problem loading/sizing the adjacent label (e.g. a slice with no
        # saved annotation yet) rather than failing the whole comparison.
        composite = adjusted
        # Load the adjacent slice's own label array unconditionally (not
        # only when the overlay is visible): right-clicking in the
        # comparison pane to pick a paint target (2026-09-22 user request)
        # needs it regardless of whether the colored overlay is currently
        # drawn on top.
        adjacent_label_array = None
        try:
            with open(adjacent_anno_path, "rb") as f:
                adjacent_label = pickle.load(f)
            adjacent_label_array = np.array(adjacent_label, dtype=np.uint32)
        except Exception:
            adjacent_label_array = None
        self._compare_adjacent_label_array = adjacent_label_array
        if self.overlay_visible and adjacent_label_array is not None:
            try:
                overlay_rgba = self._build_label_overlay_rgba(adjacent_label_array)
                if overlay_rgba.shape[:2] != adjusted.shape[:2]:
                    overlay_rgba = cv2.resize(
                        overlay_rgba,
                        (adjusted.shape[1], adjusted.shape[0]),
                        interpolation=cv2.INTER_NEAREST,
                    )
                dapi_bgr = cv2.cvtColor(adjusted, cv2.COLOR_GRAY2BGR)
                blend = (overlay_rgba[..., 3].astype(np.float32) / 255.0) * (
                    self.opacity / 255.0
                )
                blended = (
                    dapi_bgr.astype(np.float32) * (1.0 - blend[..., None])
                    + overlay_rgba[..., :3].astype(np.float32) * blend[..., None]
                )
                # overlay_rgba's first 3 channels are (B, G, R) (matches Qt
                # ARGB32, see _build_label_overlay_rgba); numpy_array_to_qimage
                # expects RGB order for a 3-channel array, so swap here.
                composite = np.ascontiguousarray(
                    blended.astype(np.uint8)[:, :, ::-1]
                )
            except Exception:
                composite = adjusted

        # Highlight the currently selected paint target within the
        # adjacent slice's own annotation only while its annotation
        # overlay is visible.  The highlight is part of that overlay,
        # rather than an independent DAPI decoration, so Toggle Overlay
        # must remove it as well (2026-09-24 user report).
        # mirrors repaint_selected_only()'s highlight for the current
        # slice's DAPI/Annotation pane, but looked up in
        # adjacent_label_array instead of current_label, so a paint
        # target selected while comparing (or one already selected
        # before Compare Adjacent was turned on) shows where it falls on
        # the adjacent tissue too, not just on the current slice.
        if (
            self.overlay_visible
            and self.selected_region_id is not None
            and adjacent_label_array is not None
        ):
            sel_mask = adjacent_label_array == self.selected_region_id
            if sel_mask.any():
                if composite.ndim == 2:
                    composite = cv2.cvtColor(composite, cv2.COLOR_GRAY2BGR)
                if sel_mask.shape != composite.shape[:2]:
                    sel_mask = cv2.resize(
                        sel_mask.astype(np.uint8),
                        (composite.shape[1], composite.shape[0]),
                        interpolation=cv2.INTER_NEAREST,
                    ).astype(bool)
                composite = composite.copy()
                # (214, 112, 218) BGR == QColor(218, 112, 214) RGB, the
                # same highlight color repaint_selected_only() paints with.
                composite[sel_mask] = (214, 112, 218)

        pixmap = QPixmap.fromImage(numpy_array_to_qimage(composite))
        if self.current_label is not None:
            # Draw at native size, no scaling at all (2026-09-23, third
            # user report): the letterbox fix (scale-to-fit +
            # center-on-canvas) removed the stretching but still resized
            # the adjacent slice to current_label's box, and adjacent
            # sections in this pipeline routinely have a genuinely
            # different registered pixel size *and* aspect ratio from the
            # current one (confirmed against real project data -- e.g.
            # (634, 310) vs (634, 508) for two sections four apart in the
            # same align leaf), not just a preview-quality mismatch. Both
            # panes already treat 1 image pixel == 1 scene unit with a
            # shared (0, 0) origin (img_pixmap for the current slice is
            # built the same way -- see _apply_lut_to_display()), so the
            # only way to actually match the current image's scale and
            # position -- as opposed to an arbitrary box -- is to *not*
            # rescale the adjacent image at all: place it at its own
            # native resolution, anchored at the same (0, 0) origin
            # current_label uses, and let the linked-view pan/zoom (which
            # already operates in shared scene coordinates, see
            # _center_linked_views()) line the two up. An opaque
            # background sized to cover at least current_label's box is
            # still needed underneath so the real annotation item
            # (z-order below this one, see _set_compare_pixmap()) never
            # shows through at the edges; it now only pads, never shrinks
            # or stretches the visible picture.
            target_w = self.current_label.shape[1]
            target_h = self.current_label.shape[0]
            native_w = pixmap.width()
            native_h = pixmap.height()
            canvas_w = max(target_w, native_w)
            canvas_h = max(target_h, native_h)
            # Where the native picture sits *within the canvas pixmap's own
            # raster* (only nonzero when the canvas is padded out to
            # target's box because native is the smaller one on that axis).
            intra_offset_x = (canvas_w - native_w) // 2
            intra_offset_y = (canvas_h - native_h) // 2
            # Where the *canvas itself* sits in the scene, relative to the
            # shared (0, 0) origin current_label's own box uses (2026-09-23
            # user report: when native is *larger* than target on an axis,
            # canvas equals native on that axis and this compare item was
            # always left positioned at the scene origin regardless --
            # correct for the smaller-native case, where canvas equals
            # target and so already starts at the same origin, but wrong
            # here: current_label's box, and so the shared linked-pan
            # center DAPI/Annotation both center on, is target_w/2 x
            # target_h/2 -- a point strictly inside the larger picture,
            # not at its center. Anchoring the canvas at the scene origin
            # regardless left the picture's own true center to the
            # bottom-right of that shared center point, which is exactly
            # the top-left-anchored skew reported). Centering the canvas
            # itself on the same (target_w/2, target_h/2) point fixes
            # both cases with one formula: it's 0 (no shift) whenever
            # canvas already equals target (the smaller-native case,
            # unchanged from before), and negative -- shifting the canvas
            # left/up so its own center lines up with target's -- whenever
            # canvas is larger (the native-larger case this fixes).
            item_x = (target_w - canvas_w) // 2
            item_y = (target_h - canvas_h) // 2
            # Recorded regardless of whether padding is actually needed
            # this time (offset stays (0, 0) then) -- _select_paint_target_
            # at_view_pos() reads these on every right-click while
            # comparing, so they must always reflect this render, not just
            # whichever offset queue entry / prior comparison last needed
            # padding. Stored as the native picture's total position in
            # *scene* coordinates (item position plus its position within
            # the canvas raster), not just the intra-canvas part, so every
            # consumer of _compare_pixmap_offset keeps working unchanged
            # regardless of where the canvas item itself now sits.
            self._compare_pixmap_offset = (
                item_x + intra_offset_x,
                item_y + intra_offset_y,
            )
            self._compare_pixmap_native_size = (native_w, native_h)
            if native_w != canvas_w or native_h != canvas_h:
                # Center the native-size picture on the padded canvas, and
                # center the canvas itself on current_label's own box
                # (2026-09-23 user reports, two follow-ups): anchoring
                # everything at (0, 0) put all of the padding on the
                # right/bottom for a smaller reference image, and left a
                # larger one skewed toward the bottom-right of the shared
                # view center -- see item_x/item_y above for the second
                # part. Neither centering step affects distortion (the
                # picture is still drawn at its own native size, unscaled)
                # or bleed-through (the canvas is still >= current_label's
                # box on both axes and still opaque, so it still fully
                # covers the real annotation item beneath it, see
                # _set_compare_pixmap()).
                #
                # The padding margin (when native is the smaller one) has
                # no pixel of its own in the adjacent slice's annotation at
                # all -- it exists only because the reference image is
                # smaller than current_label's box. 2026-09-23 user request
                # (reconsidered, same day): treated as Lost in Warp, then
                # plain black -- now matches the DAPI/Annotation panes' own
                # pan-margin background instead, since that margin is
                # conceptually the same thing ("no image here") and a
                # hardcoded black no longer agreed with it visually. Read
                # directly from the viewport's own palette/backgroundRole
                # rather than hardcoding a color, so this keeps matching
                # automatically if the app's theme (or a future dark/light
                # toggle) ever changes it.
                viewport = self.anno_view.viewport()
                outside_area_color = viewport.palette().color(
                    viewport.backgroundRole()
                )
                canvas = QPixmap(canvas_w, canvas_h)
                canvas.fill(outside_area_color)
                painter = QPainter(canvas)
                painter.drawPixmap(intra_offset_x, intra_offset_y, pixmap)
                painter.end()
                pixmap = canvas
        else:
            item_x = item_y = 0

        if not self._compare_mode_active:
            # First entry this round. Allow Adjustment is deliberately
            # left as-is (2026-09-22 user request): the current slice
            # must stay editable while comparing, e.g. via the DAPI
            # pane. Painting is still blocked specifically on the
            # comparison pane itself -- see the MouseButtonPress guard
            # in eventFilter() -- since that pane is temporarily showing
            # a different slice's image, not the current annotation.
            if not self.annotation_map_toggle.isChecked():
                # The Annotation map pane is hidden; show it so the
                # comparison is actually visible (2026-09-22 user
                # request), and remember to hide it again on exit.
                self._compare_prev_annotation_map_checked = False
                self.annotation_map_toggle.setChecked(True)
            self._compare_mode_active = True

        self._set_compare_pixmap(pixmap)
        self._compare_pixmap_item.setPos(item_x, item_y)
        what = "DAPI + annotation" if self.overlay_visible else "DAPI"
        self.status_bar.showMessage(
            f"Comparing: {adjacent_slice_id} {what} (comparison pane is "
            "read-only) -- toggle "
            '"Compare Adjacent" off to return to the annotation map.'
        )
        return True

    def _exit_compare_mode(self):
        if not self._compare_mode_active:
            return
        self._compare_mode_active = False
        self._compare_adjacent_label_array = None
        self._compare_adjacent_slice_id = None
        self._compare_pixmap_offset = (0, 0)
        self._compare_pixmap_native_size = (0, 0)
        self._hide_compare_pixmap()
        if self._compare_prev_annotation_map_checked is False:
            self.annotation_map_toggle.setChecked(False)
        self._compare_prev_annotation_map_checked = None
        self.refresh_drawings()
        self.status_bar.clearMessage()

    def _refresh_compare_if_active(self):
        """Re-render the Compare Adjacent pane against the current
        offset/channel/LUT/overlay settings (2026-09-23 user request):
        channel switches, Toggle Overlay, and the black/white/gamma
        sliders all affect what the main DAPI pane shows, and the
        comparison pane is meant to mirror that -- see the "reuse the
        main pane's LUT/channel" comment in _enter_compare_mode(). Call
        sites that already affect the main pane call this afterward so
        the comparison stays in sync instead of freezing at whatever it
        looked like when Compare Adjacent was first turned on."""
        if self._compare_mode_active:
            self._enter_compare_mode()

    def refresh_drawings(self):
        """Redraw annotation overlay from current_label without changing region IDs."""
        self.show_image_with_overlay()

    def update_zoom(self):
        self._apply_zoom(self.zoom_slider.value())

    def _text_input_focused(self) -> bool:
        return isinstance(QApplication.focusWidget(), QLineEdit)

    def _view_shortcuts_allowed(self) -> bool:
        return (
            not self.is_drawing
            and not self._is_panning
            and not self._text_input_focused()
        )

    def _nudge_zoom(self, delta_percent: int):
        if not self._view_shortcuts_allowed():
            return
        value = max(50, min(1000, self.zoom_slider.value() + delta_percent))
        self._apply_zoom(value)

    def _nudge_brush(self, delta: int):
        """Shrink/enlarge the paint brush via keyboard ('-'/'=').

        Mirrors _nudge_zoom()'s guard/clamp shape -- disabled mid-stroke or
        mid-pan/text-entry, clamped to brush_slider's own range so a fast
        key-repeat can never push brush_size out of bounds.
        """
        if not self._view_shortcuts_allowed():
            return
        value = max(
            self.brush_slider.minimum(),
            min(self.brush_slider.maximum(), self.brush_slider.value() + delta),
        )
        self.brush_slider.setValue(value)

    def _pan_views(self, fx: float, fy: float):
        """Pan both panes by ~20% of the visible viewport."""
        if not self._view_shortcuts_allowed():
            return
        vp = self.img_view.viewport()
        dx = int(round(fx * 0.2 * vp.width()))
        dy = int(round(fy * 0.2 * vp.height()))
        self._pan_by_pixels(dx, dy)

    def _pan_by_pixels(self, dx: int, dy: int):
        """Pan both panes by a shared viewport-pixel delta.

        Adjusts each view's own QScrollBar value directly -- exact integer
        arithmetic, no scene-coordinate round trip -- instead of re-deriving
        a scene "center" point via _viewport_center_scene_pos() and
        re-applying centerOn() to both views (2026-09-23 user report:
        right-click drag-pan drifted toward the top-left over the course of
        a drag). _viewport_center_scene_pos() is built on QRect.center(),
        which integer-truncates for an even viewport size -- a half-pixel
        bias already documented as a drift source for _apply_zoom() and
        _sync_scroll_from() (see their comments), both of which re-center
        only once per user action. This call site re-centered on *every*
        mouse-move event of a drag, so the same half-pixel bias compounded
        every few pixels of movement into a visible directional drift
        instead of a one-off pixel or two. QScrollBar values are already in
        viewport-pixel units, so dx/dy need no scale conversion either.
        """
        if dx == 0 and dy == 0:
            return
        self._syncing_scroll = True
        try:
            for view in (self.img_view, self.anno_view):
                hbar = view.horizontalScrollBar()
                vbar = view.verticalScrollBar()
                hbar.setValue(hbar.value() + dx)
                vbar.setValue(vbar.value() + dy)
        finally:
            self._syncing_scroll = False

    def keyPressEvent(self, event):
        super().keyPressEvent(event)

    def keyReleaseEvent(self, event):
        if event.key() == Qt.Key.Key_Space and not event.isAutoRepeat():
            self._set_space_navigation(False)
        super().keyReleaseEvent(event)

    def _fill_selected_label_with_liw(self):
        """Fill all pixels of the selected label with Lost in Warp (id 0)."""
        if not self._view_shortcuts_allowed():
            return
        if not self.allow_adjustment.isChecked():
            return
        if self.selected_region_id is None or self.current_label is None:
            return
        rid = int(self.selected_region_id)
        if rid == 0:
            return
        before = self.current_label.copy()
        mask = self.current_label == rid
        if not np.any(mask):
            return
        self.current_label[mask] = 0
        self._push_relabel_undo(before, self.current_label)
        self.was_changed = True
        self.set_paint_region(0, "LIW", "Lost in Warp")
        self.show_image_with_overlay()

    def _install_view_shortcuts(self):
        def bind(keys, slot):
            created = []
            for key in keys:
                sc = QShortcut(QKeySequence(key), self)
                sc.setContext(Qt.ShortcutContext.WindowShortcut)
                sc.activated.connect(slot)
                created.append(sc)
            return created

        # '-'/'=' are the brush size shortcuts (2026-09-18 user request);
        # zoom in/out moved to '['/']' to free them up. Plain scroll-wheel
        # zoom over the image/label views (see eventFilter's Wheel handling)
        # is unaffected and remains the primary way to zoom.
        bind(["[", "BracketLeft"], lambda: self._nudge_zoom(-10))
        bind(["]", "BracketRight"], lambda: self._nudge_zoom(10))
        bind(["-", "Minus", "KeypadMinus"], lambda: self._nudge_brush(-1))
        bind(["=", "+", "Plus", "KeypadPlus"], lambda: self._nudge_brush(1))
        bind(["Left"], lambda: self._pan_views(-1, 0))
        bind(["Right"], lambda: self._pan_views(1, 0))
        bind(["Up"], lambda: self._pan_views(0, -1))
        bind(["Down"], lambda: self._pan_views(0, 1))
        bind(["Delete", "Backspace"], self._fill_selected_label_with_liw)
        # Toggle Overlay shortcut (2026-09-19 user request). Guarded by
        # _view_shortcuts_allowed() like the other bindings above -- Tab is
        # also the standard focus-advance key, so this must not fire while
        # a text field (e.g. the Go to... dialog, a spin box) has focus and
        # the user is just tabbing between fields.
        overlay_shortcuts = bind(["Tab"], self._toggle_overlay_shortcut)
        # Annotation map shortcut (2026-09-19 user request).
        annotation_map_shortcuts = bind(
            ["`", "QuoteLeft", "AsciiTilde"], self._toggle_annotation_map_shortcut
        )
        # Undo shortcut (2026-09-20 user request): Ctrl+Z mirrors the Undo
        # button (undo_last_delta()). Guarded by _view_shortcuts_allowed()
        # like most other bindings above (not the Toggle Overlay-style
        # bypass) -- unlike toggling overlay visibility, undoing a delta
        # mutates current_label/deltas/originals, which is exactly the
        # kind of state a mid-stroke is_drawing or an active pan drag must
        # not be interrupted by. Also folded into
        # _text_focus_dedicated_shortcuts below: Ctrl+Z is QLineEdit's own
        # built-in text-undo shortcut, so this QShortcut must be disabled
        # while a text field has focus, the same way Tab/backtick are,
        # or it would eat the keystroke before the QLineEdit's native
        # undo ever sees it.
        undo_shortcuts = bind(["Ctrl+Z"], self._undo_shortcut)
        # 2026-09-19 follow-up: the guard functions above only skip *acting*
        # on Tab/backtick while a text field has focus -- they don't stop
        # the QShortcut itself from consuming the keystroke first. A plain
        # QShortcut with WindowShortcut context intercepts a matching key
        # before it ever reaches the focused widget, so while typing in
        # (say) the Region picker's Search box, Tab silently failed to
        # advance focus and "`" silently failed to type its character --
        # both keys were "used only as shortcuts" in the wrong sense: they
        # ate the keystroke even when guarded out, rather than falling
        # back to their normal behavior. Fix: track these specific
        # QShortcut objects and toggle .setEnabled() on focus changes --
        # disabled, a QShortcut does not intercept its key at all, so the
        # keystroke reaches the focused widget exactly as if no shortcut
        # were registered, restoring normal Tab-advance / literal "`"
        # typing there. Outside any text field they stay enabled, so
        # nothing changes about how they behave elsewhere in the window.
        self._text_focus_dedicated_shortcuts = (
            overlay_shortcuts + annotation_map_shortcuts + undo_shortcuts
        )
        self._sync_text_focus_dedicated_shortcuts()
        QApplication.instance().focusChanged.connect(
            self._sync_text_focus_dedicated_shortcuts
        )

    def _sync_text_focus_dedicated_shortcuts(self, *_args):
        enabled = not self._text_input_focused()
        for sc in getattr(self, "_text_focus_dedicated_shortcuts", []):
            sc.setEnabled(enabled)

    def _toggle_overlay_shortcut(self):
        # 2026-09-19 user request: unlike the other view shortcuts, Toggle
        # Overlay should still fire mid-brush-stroke (self.is_drawing) --
        # toggling the overlay pixmap's visibility doesn't touch
        # current_label or the in-progress stroke, it only hides/shows the
        # QGraphicsPixmapItem, so there's nothing unsafe about it firing
        # while painting. Deliberately does NOT reuse
        # _view_shortcuts_allowed() (that still guards the other shortcuts
        # against is_drawing) -- only view panning and text-field focus are
        # still checked here, both of which remain genuine conflicts (a
        # panning drag has its own left/middle-button semantics; a focused
        # text field should keep every bare-key shortcut, including this
        # one, out of its way).
        if self._is_panning or self._text_input_focused():
            return
        self.toggle_overlay()

    def _toggle_annotation_map_shortcut(self):
        if not self._view_shortcuts_allowed():
            return
        self.annotation_map_toggle.toggle()

    def _undo_shortcut(self):
        if not self._view_shortcuts_allowed():
            return
        self.undo_last_delta()

    def update_brush(self):
        self.brush_size = self.brush_slider.value()
        self._update_paint_target_strip()
        self._refresh_brush_cursor_at_mouse()

    def _refresh_brush_cursor_at_mouse(self) -> None:
        """Redraw the brush-size ring at the cursor's current position.

        _update_brush_cursor() is normally only called from eventFilter()'s
        mouse-move handling, so a brush-size change from something other
        than mouse movement (the -/= keyboard shortcuts added 2026-09-18,
        or a manual slider drag while the mouse sits still over the image)
        left the visible ring at its old size until the mouse next moved.
        Locates whichever pane (img_view/anno_view) the cursor currently
        sits over from the global cursor position and re-issues the same
        update eventFilter() would have made."""
        for view in (self.img_view, self.anno_view):
            local_pos = view.viewport().mapFromGlobal(QCursor.pos())
            if view.viewport().rect().contains(local_pos):
                self._update_brush_cursor(view, view.mapToScene(local_pos))
                return

    def convert_to_parents(self):
        """Quick rollup: cortical layers → functional areas (this section only)."""
        if not self.catalog:
            return
        self.parcel_tier_combo.blockSignals(True)
        for i in range(self.parcel_tier_combo.count()):
            if self.parcel_tier_combo.itemData(i) == "areas":
                self.parcel_tier_combo.setCurrentIndex(i)
                break
        self.parcel_tier_combo.blockSignals(False)
        self.parcel_tier_id = "areas"
        self._apply_parcellation_from_baseline(
            tier_id="areas",
            st_level=None,
            update_metadata=True,
        )

    def update_opacity(self):
        self.opacity = self.opacity_slider.value()
        self._set_img_overlay_layer(self._display_overlay_pixmap())

    def toggle_overlay(self, visible: bool | None = None):
        """Set overlay visibility and keep its header toggle state in sync."""
        if visible is None:
            visible = not self.overlay_visible
        self.overlay_visible = bool(visible)
        button = getattr(self, "overlay_toggle", None)
        if button is not None and button.isChecked() != self.overlay_visible:
            button.blockSignals(True)
            button.setChecked(self.overlay_visible)
            button.blockSignals(False)
        self._set_img_overlay_layer(self._display_overlay_pixmap())
        self.repaint_selected_only()
        self._refresh_compare_if_active()

    def _build_label_overlay_rgba(self, label_array):
        """Map a label array to a colored BGRA overlay (Qt ARGB32 byte
        order: B, G, R, A) with 1px boundary outlines between regions.

        Factored out of show_image_with_overlay() (2026-09-22) so Compare
        Adjacent can build the same colored overlay for an *adjacent*
        slice's label array -- reusing this instead of re-deriving the
        label-color lookup keeps both call sites in sync with
        resolve_label_color()/add_outlines() automatically.
        """
        with perf_log.perf_section("adjust.overlay.build"):
            present = np.unique(label_array)
            table = np.zeros((present.shape[0], 4), dtype=np.uint8)
            for i, lid in enumerate(present):
                if int(lid) == 0:
                    table[i] = (0, 0, 0, 255)
                else:
                    r, g, b = resolve_label_color(
                        int(lid), self.structure_map, self.catalog
                    )[:3]
                    table[i] = (b, g, r, 255)
            idx = np.searchsorted(present, label_array)
            overlay_rgba = np.ascontiguousarray(table[idx])
        with perf_log.perf_section("adjust.overlay.outlines"):
            overlay_rgba = add_outlines(label_array, overlay_rgba)
        return overlay_rgba

    def show_image_with_overlay(self):
        # Preserve the viewport's exact scroll position across this rebuild
        # (2026-09-19 user report, 2nd round: Refresh and Undo -- both call
        # this method directly, not through _apply_lut_to_display() -- still
        # drifted the slice's on-screen position even after that method's
        # own fix for the LUT slider/Seam toggle/Auto/Reset case). Same root
        # cause, same fix: save each view's raw scrollbar pixel values
        # up front and restore them byte-exact at the very end, instead of
        # the scene-coordinate capture/centerOn() round-trip below (which
        # this method still uses for its own internal purposes -- restoring
        # the saved values afterward simply overrides whatever that
        # round-trip's rounding produced). See _apply_lut_to_display()'s
        # docstring for the full explanation of why centerOn() alone isn't
        # enough.
        #
        # Skipped on this call being the very first display for this scene
        # (self._pan_scene_initialized still False below): there is no
        # prior position to preserve yet, and the explicit image-center
        # centerOn() a few lines down is the intended initial placement,
        # not drift to undo.
        # A pending size-change recentre (see _load_section_at()) overrides
        # the usual scroll-preserving behavior for this one call only --
        # the old scrollbar pixel values belong to the previous slice's
        # (differently sized) image and no longer mean the same on-screen
        # position on this one.
        force_recenter = self._recenter_next_render
        preserve_scroll = self._pan_scene_initialized and not force_recenter
        if preserve_scroll:
            scrollbar_state = [
                (
                    view.horizontalScrollBar().value(),
                    view.verticalScrollBar().value(),
                )
                for view in (self.img_view, self.anno_view)
            ]
        center = self._viewport_center_scene_pos(self.img_view)
        label_array = np.array(self._label_for_display(), dtype=np.uint32)
        anno_as_array = self._build_label_overlay_rgba(label_array)
        self._anno_rgba = anno_as_array  # cache for incremental stroke refresh
        anno_image = numpy_array_to_qimage(anno_as_array)
        self.anno_pixmap = QPixmap.fromImage(anno_image)
        self._set_anno_pixmap(self.anno_pixmap)
        self._set_img_overlay_layer(self._display_overlay_pixmap())

        self._set_img_overlay_layer(self._display_overlay_pixmap())

        self._overlay_ready = True
        self.repaint_selected_only()
        self._update_paint_resolution_warning()
        if not self._pan_scene_initialized and not self.img_pixmap.isNull():
            # The virtual scene is larger than the image, so explicitly centre
            # the first display on image pixels rather than the old empty scene.
            center = QPointF(
                self.img_pixmap.width() / 2.0,
                self.img_pixmap.height() / 2.0,
            )
            self._pan_scene_initialized = True
        elif force_recenter and not self.img_pixmap.isNull():
            center = QPointF(
                self.img_pixmap.width() / 2.0,
                self.img_pixmap.height() / 2.0,
            )
        self._recenter_next_render = False
        self._apply_zoom(self.zoom_level, center_scene_pos=center)
        if preserve_scroll:
            self._syncing_scroll = True
            try:
                for view, (h_value, v_value) in zip(
                    (self.img_view, self.anno_view), scrollbar_state
                ):
                    view.horizontalScrollBar().setValue(h_value)
                    view.verticalScrollBar().setValue(v_value)
            finally:
                self._syncing_scroll = False

    def _finalize_stroke_overlay(self, x0, y0, x1, y1):
        """Rebuild the overlay only inside the stroke bounding box (plus a small
        margin), instead of re-color-mapping the whole full-res label array. This
        keeps stroke-release cheap. Mirrors show_image_with_overlay +
        repaint_selected_only, restricted to the region that changed."""
        m = 2
        h, w = self.current_label.shape
        x0 = max(0, x0 - m)
        y0 = max(0, y0 - m)
        x1 = min(w, x1 + m)
        y1 = min(h, y1 + m)
        if x1 <= x0 or y1 <= y0:
            return
        sub = np.array(
            self._label_for_display()[y0:y1, x0:x1], dtype=np.uint32
        )
        present = np.unique(sub)
        table = np.zeros((present.shape[0], 4), dtype=np.uint8)
        for i, lid in enumerate(present):
            if int(lid) == 0:
                table[i] = (0, 0, 0, 255)
            else:
                r, g, b = resolve_label_color(
                    int(lid), self.structure_map, self.catalog
                )[:3]
                table[i] = (b, g, r, 255)
        idx = np.searchsorted(present, sub)
        base_patch = add_outlines(sub, np.ascontiguousarray(table[idx]))
        # Keep the unhighlighted cache current.  A later right-click restores
        # the previous paint target from this cache, so leaving it at the
        # pre-stroke state would visually erase newly painted pixels.
        if (
            self._anno_rgba is not None
            and self._anno_rgba.shape[:2] == self.current_label.shape
        ):
            self._anno_rgba[y0:y1, x0:x1] = base_patch
        patch = base_patch.copy()
        # Selected-region highlight (purple), matching repaint_selected_only.
        if self.selected_region_id is not None:
            sel = sub == self.selected_region_id
            patch[sel] = (214, 112, 218, 255)  # QColor(218,112,214) in B,G,R,A
        patch_img = numpy_array_to_qimage(patch)
        anno = self._anno_pixmap()
        if anno is None or anno.isNull():
            self.show_image_with_overlay()
            return
        painter = QPainter(anno)
        painter.drawImage(x0, y0, patch_img)
        painter.end()
        self._set_anno_pixmap(anno)
        self._set_img_overlay_layer(self._display_overlay_pixmap())

    def _finalize_last_stroke(self):
        """Local overlay refresh for the just-finished stroke; full rebuild if the
        stroke set is unavailable."""
        if self.current_delta <= 0 or self.current_delta - 1 >= len(self.deltas):
            self.show_image_with_overlay()
            return
        stroke = self.deltas[self.current_delta - 1]
        if not stroke:
            self.show_image_with_overlay()
            return
        if isinstance(stroke, dict) and stroke.get("kind") == "capsule":
            bbox = stroke.get("bbox")
            if bbox is None:
                self.show_image_with_overlay()
                return
            with perf_log.perf_section("adjust.overlay.finalize_local"):
                self._finalize_stroke_overlay(*bbox)
            return
        pts = np.asarray(tuple(stroke), dtype=np.int64)
        x0 = int(pts[:, 0].min())
        x1 = int(pts[:, 0].max()) + 1
        y0 = int(pts[:, 1].min())
        y1 = int(pts[:, 1].max()) + 1
        with perf_log.perf_section("adjust.overlay.finalize_local"):
            self._finalize_stroke_overlay(x0, y0, x1, y1)

    def paint_deltas(self, points):
        if self._anno_pixmap_item is None or not points:
            return
        with perf_log.perf_section("adjust.drag.paint_dirty"):
            self._anno_pixmap_item.paint_points(points)
            overlay = self._img_overlay_item
            if overlay is not None and overlay.image.size() == self._anno_pixmap_item.image.size():
                overlay.paint_points(points)
            else:
                # Preserve exact scaling for mixed-resolution channels.
                self._set_img_overlay_layer(self._display_overlay_pixmap())

    def _poll_save_exit(self) -> None:
        try:
            if self._save_exit_flag.is_file():
                self._save_exit_flag.unlink()
                set_viewer_exit_reason("cancel")
                self.close()
        except OSError:
            pass

    def closeEvent(self, event):
        if self.was_changed:
            if not self.warn_unsaved_changes():
                event.ignore()
                return
        if get_viewer_exit_reason() != "cancel":
            set_viewer_exit_reason("done")
        self._save_exit_timer.stop()
        self._cancel_dapi_prefetch()
        self._dapi_prefetch_executor.shutdown(wait=False, cancel_futures=True)
        # A worker already writing its temporary PNG cannot be force-stopped.
        # Leave TemporaryDirectory's finalizer in charge in that narrow case.
        if self._dapi_prefetch_future is None or self._dapi_prefetch_future.done():
            self._dapi_live_cache_tempdir.cleanup()
        super().closeEvent(event)

    def warn_unsaved_changes(self):
        dialog = QMessageBox(self)
        dialog.setIcon(QMessageBox.Icon.Critical)
        dialog.setText("You have unsaved changes!")
        dialog.setInformativeText("Do you want to save your changes?")
        dialog.setStandardButtons(
            QMessageBox.StandardButton.Save
            | QMessageBox.StandardButton.Discard
            | QMessageBox.StandardButton.Cancel
        )
        dialog.setDefaultButton(QMessageBox.StandardButton.Save)
        ret = dialog.exec()
        if ret == QMessageBox.StandardButton.Save:
            self.save_changes()
            return True
        elif ret == QMessageBox.StandardButton.Discard:
            return True

    def save_changes(self):
        if not is_suppressed(KEY_CONFIRM_SAVE_OVERWRITE):
            dialog = QMessageBox(self)
            dialog.setIcon(QMessageBox.Icon.Information)
            dialog.setText("Are you sure you want to save your changes?")
            dialog.setInformativeText(
                "This will overwrite the current annotation file."
            )
            dialog.setStandardButtons(
                QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Cancel
            )
            dialog.setDefaultButton(QMessageBox.StandardButton.Save)
            dont_show = QCheckBox("Don't show this warning again")
            dialog.setCheckBox(dont_show)
            ret = dialog.exec()
            if ret == QMessageBox.StandardButton.Cancel:
                return
            if dont_show.isChecked():
                set_suppressed(KEY_CONFIRM_SAVE_OVERWRITE, True)

        # Save the current label
        _, anno_path, slice_id = self.pairs[self.current_index]
        if self.current_label is not None and not np.any(self.current_label):
            if not self._confirm_save_empty_annotation(slice_id):
                return
        with open(anno_path, "wb") as f:
            pickle.dump(self.current_label, f)
        self.was_changed = False
        self._update_parcellation_labels()
        self._refresh_annotation_label_audit_cache(show_intensity_notice=True)

    def _confirm_save_empty_annotation(self, slice_id: str) -> bool:
        """Block silently overwriting a saved annotation with an all-background
        one (2026-09-23: M581-01(50)/(51)/(52)/(64) lost all labeled regions
        this way -- most likely several uses of the Delete/Backspace "fill
        selected region with Lost in Warp" shortcut across most regions on
        each section, then Save/"Save" on the unsaved-changes prompt, with no
        warning that the result was now fully empty). Always shown regardless
        of the KEY_CONFIRM_SAVE_OVERWRITE "don't ask again" setting, since
        that setting is about the routine "this overwrites the file" notice,
        not about this specific data-loss shape."""
        dialog = QMessageBox(self)
        dialog.setIcon(QMessageBox.Icon.Warning)
        dialog.setWindowTitle("Annotation is empty")
        dialog.setText(
            f"{slice_id}: this annotation has no labeled regions at all "
            "(100% background)."
        )
        dialog.setInformativeText(
            "Saving now will overwrite the saved annotation file with a "
            "completely empty one. If this is unexpected, click Cancel and "
            "check whether regions were accidentally cleared (e.g. with "
            "Delete/Backspace), or use Restore Fine Parcellation to recover "
            "from the full-detail backup if one exists."
        )
        dialog.setStandardButtons(
            QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Cancel
        )
        dialog.setDefaultButton(QMessageBox.StandardButton.Cancel)
        return dialog.exec() == QMessageBox.StandardButton.Save

    def _refresh_annotation_label_audit_cache(self, show_intensity_notice: bool = False):
        from annotation_label_audit import (
            audit_align_leaf,
            audit_label_array,
            write_audit_cache,
        )
        from annotation_relabel import get_slice_parcellation

        if not self.catalog:
            return
        try:
            audit = audit_align_leaf(
                self.annotation_dir, self.catalog, self.structure_map
            )
            write_audit_cache(self.annotation_dir, audit)
        except OSError:
            return
        self._update_paint_resolution_warning()
        if not show_intensity_notice:
            return
        entry = get_slice_parcellation(self.annotation_dir, self._current_slice_id())
        slice_audit = audit_label_array(
            self.current_label, self.catalog, self.structure_map, entry
        )
        if not slice_audit.get("issues"):
            return
        if is_suppressed(KEY_ISOLATE_LABEL_AUDIT):
            return
        dialog = QMessageBox(self)
        dialog.setIcon(QMessageBox.Icon.Warning)
        dialog.setWindowTitle("Isolate Regions")
        dialog.setText("Saved labels may affect Isolate Regions")
        dialog.setInformativeText(
            "This section has mixed or mismatched label resolution. Re-run Isolate "
            "Regions after fixing annotations, or review Include cortical layers on "
            "the setup page."
        )
        dialog.setStandardButtons(QMessageBox.StandardButton.Ok)
        dont_show = QCheckBox("Don't show this warning again")
        dialog.setCheckBox(dont_show)
        dialog.exec()
        if dont_show.isChecked():
            set_suppressed(KEY_ISOLATE_LABEL_AUDIT, True)

    def _load_section_at(self, index):
        """Load and display the section at ``index`` (shared by prev/next/goto)."""
        if index < 0 or index >= len(self.pairs):
            return
        # Compare Adjacent stays on across navigation (2026-09-23 user
        # request): remember it was active and refresh it against the
        # *new* current section (same offset) at the end of this method,
        # instead of dropping out of compare mode on every prev/next/goto.
        was_comparing = self._compare_mode_active
        self._cancel_dapi_prefetch()
        # 2026-09-23 user request: recentre on the new slice's image
        # whenever its pixel size differs from the one just displayed --
        # captured before rebuild_channel_combo() below overwrites
        # self.img_pixmap with the new slice's image, so this is really
        # the *previous* slice's size at the moment of comparison.
        prev_img_size = (
            (self.img_pixmap.width(), self.img_pixmap.height())
            if self.img_pixmap is not None and not self.img_pixmap.isNull()
            else None
        )
        self.current_index = index
        _, anno_path, slice_id = self.pairs[self.current_index]
        with perf_log.perf_section("adjust.nav.load_label"):
            with open(anno_path, "rb") as f:
                self.current_label = pickle.load(f)

        ensure_full_backup(self.annotation_dir, slice_id, self.current_label)
        self.current_delta = 0
        self.deltas = []
        self.originals = []
        self.was_changed = False
        self._update_section_labels()
        self._sync_parcellation_ui_from_metadata()
        with perf_log.perf_section("adjust.nav.rebuild_channels"):
            self.rebuild_channel_combo()
        new_img_size = (
            (self.img_pixmap.width(), self.img_pixmap.height())
            if self.img_pixmap is not None and not self.img_pixmap.isNull()
            else None
        )
        if (
            prev_img_size is not None
            and new_img_size is not None
            and prev_img_size != new_img_size
        ):
            self._recenter_next_render = True
        with perf_log.perf_section("adjust.nav.render"):
            self.show_image_with_overlay()
        if was_comparing:
            self._enter_compare_mode()

    def prev_image(self):
        if self.current_index > 0:
            if self.was_changed and not self.warn_unsaved_changes():
                return
            self._load_section_at(self.current_index - 1)

    def next_image(self):
        if self.current_index < len(self.pairs) - 1:
            if self.was_changed and not self.warn_unsaved_changes():
                return
            self._load_section_at(self.current_index + 1)

    def goto_image(self):
        """Jump directly to a chosen section (by slice id)."""
        if len(self.pairs) <= 1:
            return
        if self.was_changed and not self.warn_unsaved_changes():
            return
        slice_ids = [str(p[2]) for p in self.pairs]
        current = self.current_index if 0 <= self.current_index < len(slice_ids) else 0
        choice, ok = QInputDialog.getItem(
            self, "Go to section", "Section:", slice_ids, current, False
        )
        if not ok or not choice:
            return
        try:
            target = slice_ids.index(choice)
        except ValueError:
            return
        if target != self.current_index:
            self._load_section_at(target)

    def view_to_image_coordinates(self, view, point):
        # Transform the point from view coordinates to scene coordinates
        scene_point = view.mapToScene(point)
        # Convert to integer QPoint
        scene_point = QPoint(int(scene_point.x()), int(scene_point.y()))
        return scene_point

    def update_status_bar_with_region(self, pos, view=None):
        # Compare Adjacent (2026-09-23 user report): hovering the
        # comparison pane must show *that* slice's region under the
        # cursor, not the current slice's -- previously this always read
        # self.current_label regardless of which pane triggered it, so
        # the status bar kept reporting the current annotation even while
        # the comparison picture was what the pointer was actually over.
        if (
            self._compare_mode_active
            and view is self.anno_view
            and self._compare_adjacent_label_array is not None
        ):
            label_value = self._compare_adjacent_label_at(pos)
            if label_value is None:
                return
            region_name = self.structure_map.get(label_value, {}).get(
                "name", "Unknown region"
            )
            slice_id = self._compare_adjacent_slice_id or "adjacent slice"
            self.status_bar.showMessage(
                f"{slice_id} (Compare Adjacent) region: {region_name}"
            )
            return
        if (
            pos.x() < 0
            or pos.y() < 0
            or pos.x() >= self.current_label.shape[1]
            or pos.y() >= self.current_label.shape[0]
        ):
            # Out of bounds
            self.status_bar.showMessage("Out of bounds")
        else:
            label_value = self.current_label[pos.y(), pos.x()]
            region_name = self.structure_map.get(label_value, {}).get(
                "name", "Unknown region"
            )
            self.status_bar.showMessage(
                f"Region: {region_name} | Selected: {self.selected_region_name}"
            )

    def points_in_circle(self, center, radius):
        """Return a list of points in a circle"""
        return [(center[0]+x, center[1]+y) for x, y in self._circle_offsets(radius)]

    @staticmethod
    @lru_cache(maxsize=8)
    def _circle_offsets(radius):
        return tuple((x, y) for x in range(-radius, radius+1)
                     for y in range(-radius, radius+1) if x*x+y*y <= radius*radius)

    def _begin_capsule_stroke(self):
        """Allocate compact per-stroke state at the current Undo position."""
        if len(self.deltas) <= self.current_delta:
            self.deltas.append({"kind": "capsule", "chunks": [], "bbox": None})
            self.originals.append([])
        self._stroke_seen = np.zeros(self.current_label.shape, dtype=bool)

    def _disable_parcel_preview_for_brush_edit(self):
        """Return to editable labels before applying a manual brush stroke."""
        if not self.parcel_preview:
            return
        self.parcel_preview = False
        self.parcel_preview_array = None
        self.parcel_preview_toggle.blockSignals(True)
        self.parcel_preview_toggle.setChecked(False)
        self.parcel_preview_toggle.blockSignals(False)
        self.show_image_with_overlay()
        self.status_bar.showMessage("Preview borders turned off for brush editing")

    def _paint_capsule_segment(self, p0, p1):
        """Paint one round-capped segment and retain array-form Undo data.

        This replaces interpolated Python circle stamps. Each segment is one
        vectorized capsule; the visit map prevents overlap from recording a
        pixel twice within the same Undo stroke.
        """
        if self.selected_region_id is None or p0 is None or p1 is None:
            return
        self._disable_parcel_preview_for_brush_edit()
        if len(self.deltas) <= self.current_delta:
            self._begin_capsule_stroke()
        if self._stroke_seen is None or self._stroke_seen.shape != self.current_label.shape:
            self._stroke_seen = np.zeros(self.current_label.shape, dtype=bool)
        stroke = self.deltas[self.current_delta]
        if not (isinstance(stroke, dict) and stroke.get("kind") == "capsule"):
            return
        h, w = self.current_label.shape
        x0, y0 = int(p0.x()), int(p0.y())
        x1, y1 = int(p1.x()), int(p1.y())
        radius = int(self.brush_size)
        pad = radius + 1
        left, right = max(0, min(x0, x1) - pad), min(w, max(x0, x1) + pad + 1)
        top, bottom = max(0, min(y0, y1) - pad), min(h, max(y0, y1) + pad + 1)
        if left >= right or top >= bottom:
            return
        yy, xx = np.ogrid[top:bottom, left:right]
        dx, dy = x1 - x0, y1 - y0
        denom = dx * dx + dy * dy
        if denom:
            t = np.clip(((xx - x0) * dx + (yy - y0) * dy) / denom, 0.0, 1.0)
            dist2 = (xx - (x0 + t * dx)) ** 2 + (yy - (y0 + t * dy)) ** 2
        else:
            dist2 = (xx - x0) ** 2 + (yy - y0) ** 2
        mask = dist2 <= radius * radius
        mask &= ~self._stroke_seen[top:bottom, left:right]
        rel_y, rel_x = np.nonzero(mask)
        if not len(rel_x):
            return
        ys, xs = rel_y + top, rel_x + left
        previous = self.current_label[ys, xs].copy()
        self.current_label[ys, xs] = self.selected_region_id
        self._stroke_seen[ys, xs] = True
        stroke["chunks"].append((ys, xs))
        self.originals[self.current_delta].append(previous)
        x_min, x_max = int(xs.min()), int(xs.max()) + 1
        y_min, y_max = int(ys.min()), int(ys.max()) + 1
        bbox = stroke["bbox"]
        stroke["bbox"] = (
            (x_min, y_min, x_max, y_max)
            if bbox is None
            else (min(bbox[0], x_min), min(bbox[1], y_min), max(bbox[2], x_max), max(bbox[3], y_max))
        )
        self.was_changed = True
        self.paint_deltas([QPoint(int(x), int(y)) for y, x in zip(ys, xs)])

    def _refresh_overlay_region(self, bbox):
        """Recompute the overlay for only the stroke's bounding box (color +
        outlines) and update the cached RGBA + pixmaps — far cheaper than the
        full-frame rebuild in show_image_with_overlay."""
        if bbox is None or self._anno_rgba is None:
            self.show_image_with_overlay()
            return
        lab = np.asarray(self._label_for_display(), dtype=np.uint32)
        h, w = lab.shape
        if self._anno_rgba.shape[0] != h or self._anno_rgba.shape[1] != w:
            self.show_image_with_overlay()
            return
        min_x, min_y, max_x, max_y = bbox
        x0 = max(0, min_x - 1)
        y0 = max(0, min_y - 1)
        x1 = min(w, max_x + 2)
        y1 = min(h, max_y + 2)
        if x1 <= x0 or y1 <= y0:
            return
        sub = lab[y0:y1, x0:x1]
        present = np.unique(sub)
        table = np.zeros((present.shape[0], 4), dtype=np.uint8)
        for i, lid in enumerate(present):
            if int(lid) == 0:
                table[i] = (0, 0, 0, 255)
            else:
                r, g, b = resolve_label_color(
                    int(lid), self.structure_map, self.catalog
                )[:3]
                table[i] = (b, g, r, 255)
        region = table[np.searchsorted(present, sub)]
        boundary = np.zeros(sub.shape, dtype=bool)
        boundary[:-1, :] |= sub[:-1, :] != sub[1:, :]
        boundary[:, :-1] |= sub[:, :-1] != sub[:, 1:]
        region[boundary] = 0
        self._anno_rgba[y0:y1, x0:x1] = region
        anno_image = numpy_array_to_qimage(self._anno_rgba)
        self.anno_pixmap = QPixmap.fromImage(anno_image)
        self._set_anno_pixmap(self.anno_pixmap)
        self._set_img_overlay_layer(self._display_overlay_pixmap())
        self.repaint_selected_only()

    def draw_on_image(self, point):
        if (
            point
            and 0 <= point.x() < self.current_label.shape[1]
            and 0 <= point.y() < self.current_label.shape[0]
        ):
            self._paint_capsule_segment(point, point)

    def undo_last_delta(self):
        if self.current_delta > 0:
            # Get the last set of points and original values
            last_points = self.deltas[self.current_delta - 1]
            last_originals = self.originals[self.current_delta - 1]

            # Restore the original values
            if isinstance(last_points, dict) and last_points.get("kind") == "capsule":
                for (ys, xs), previous in zip(last_points["chunks"], last_originals):
                    self.current_label[ys, xs] = previous
            else:
                for p in last_points:
                    self.current_label[p[1], p[0]] = last_originals[p]

            # Remove the last delta and originals from the tracking
            self.deltas.pop(self.current_delta - 1)
            self.originals.pop(self.current_delta - 1)
            self.current_delta -= 1  # Decrease the current delta index

            # Reflect the changes in the image
            self.show_image_with_overlay()

    def repaint_selected_only(self):
        """Repaint the selected region only.

        Vectorized bounding-box patch instead of a per-pixel
        QPainter.drawPoints loop (2026-09-18 user report: selecting "Lost
        in Warp" as the paint target was slow). The old loop built one
        QPoint Python object per matching pixel -- O(matched pixels) in
        pure Python, not numpy -- which was tolerable for a small
        anatomical region but very slow for LIW (id 0), which by
        definition is usually the largest-area label in a warped slice.
        Mirrors the pattern already used for the full overlay build
        (show_image_with_overlay()'s searchsorted/gather comment) and for
        _restore_region_highlight()'s undo path, which already avoided
        this exact anti-pattern.
        """
        if (
            not self._overlay_ready
            or not hasattr(self, "anno_pixmap")
            or self.anno_pixmap is None
            or self.anno_pixmap.isNull()
        ):
            return

        mask = self.current_label == self.selected_region_id
        ys, xs = np.where(mask)
        if not len(xs):
            return

        anno_pixmap = self._anno_pixmap()
        if anno_pixmap is None:
            return

        if (
            self._anno_rgba is not None
            and self._anno_rgba.shape[:2] == self.current_label.shape
        ):
            left, right = int(xs.min()), int(xs.max()) + 1
            top, bottom = int(ys.min()), int(ys.max()) + 1
            patch = self._anno_rgba[top:bottom, left:right].copy()
            local_mask = mask[top:bottom, left:right]
            patch[local_mask] = (214, 112, 218, 255)  # QColor(218,112,214) in B,G,R,A
            painter = QPainter(anno_pixmap)
            painter.drawImage(left, top, numpy_array_to_qimage(patch))
            painter.end()
        else:
            # Fallback for the rare case _anno_rgba isn't populated/in sync
            # with current_label yet -- same behavior as before, just no
            # longer the common path.
            painter = QPainter(anno_pixmap)
            color = QColor(218, 112, 214)
            painter.setPen(color)
            points = [QPoint(int(j), int(i)) for i, j in zip(ys, xs)]
            painter.drawPoints(points)
            painter.end()

        self._set_anno_pixmap(anno_pixmap)
        self._set_img_overlay_layer(self._display_overlay_pixmap())

    def _sync_navigation_cursor(self):
        active = self._space_down or self._is_panning
        for view in (self.img_view, self.anno_view):
            vp = view.viewport()
            if active:
                if not hasattr(vp, '_saved_navigation_cursor'):
                    vp._saved_navigation_cursor = vp.cursor()
                vp.setCursor(Qt.CursorShape.ClosedHandCursor if self._is_panning
                             else Qt.CursorShape.OpenHandCursor)
            elif hasattr(vp, '_saved_navigation_cursor'):
                vp.setCursor(vp._saved_navigation_cursor)
                del vp._saved_navigation_cursor
        if active:
            self._brush_cursor_img.hide()
            self._brush_cursor_anno.hide()

    def _set_space_navigation(self, enabled):
        if enabled and self.is_drawing:
            # Finish the edit before switching tools, preserving its undo entry.
            self.is_drawing = False
            self.last_draw_point = None
            self.current_delta += 1
            self._update_parcellation_labels()
            self._finalize_last_stroke()
            self._stroke_seen = None
        self._space_down = enabled
        if not enabled and self._space_pan_active:
            self._is_panning = False
            self._space_pan_active = False
            self._pan_last_pos = None
        self._sync_navigation_cursor()

    def _compare_adjacent_label_at(self, image_point) -> int | None:
        """Look up the region id under *image_point* (current_label-space
        coordinates, i.e. what view_to_image_coordinates() already
        returns) for Compare Adjacent. Shared by the right-click paint-
        target picker and the hover status bar (2026-09-23 user report:
        hovering the comparison pane still showed the *current* slice's
        region info in the status bar, not the adjacent slice's -- this
        lookup previously lived only inside
        _select_paint_target_at_view_pos()).

        The black padding margin around a reference image smaller than
        current_label's own box has no pixel of its own in the adjacent
        slice's annotation -- it is pure canvas fill, not part of the
        picture (see _enter_compare_mode()). 2026-09-23, reconsidered
        same-day follow-up: an earlier revision treated it as Lost in
        Warp (id 0); reverted, since it isn't a real warped-out area,
        just "no reference image here" -- it now gets the same "outside"
        treatment (None, after showing an explanatory status message) as
        a point off the canvas entirely. _pointer_in_pane_bounds() already
        screens out both cases for every caller reached through it, so
        this bounds check is mainly a defensive fallback here.
        """
        if not self._is_inside_compare_image(image_point):
            self.status_bar.showMessage(
                "Outside the adjacent slice's image (Compare Adjacent)."
            )
            return None
        adjacent_array = self._compare_adjacent_label_array
        offset_x, offset_y = self._compare_pixmap_offset
        native_w, native_h = self._compare_pixmap_native_size
        pic_x = image_point.x() - offset_x
        pic_y = image_point.y() - offset_y
        if adjacent_array.shape == (native_h, native_w):
            return int(adjacent_array[pic_y, pic_x])
        src_x = int(pic_x * adjacent_array.shape[1] / native_w)
        src_y = int(pic_y * adjacent_array.shape[0] / native_h)
        src_x = min(max(src_x, 0), adjacent_array.shape[1] - 1)
        src_y = min(max(src_y, 0), adjacent_array.shape[0] - 1)
        return int(adjacent_array[src_y, src_x])

    def _select_paint_target_at_view_pos(self, view, point):
        """Select the atlas label at a click that did not become a right-drag."""
        image_point = self.view_to_image_coordinates(view, point)
        # 2026-09-23 user report: right-clicking the part of a Compare
        # Adjacent reference picture that extends past current_label's own
        # box (the reference slice's native image can be larger, see
        # _enter_compare_mode()) always fell through to "Outside DAPI
        # image" here -- this guard checked current_label's bounds
        # unconditionally, before the Compare Adjacent-aware branch below
        # ever got a chance to run. Routed through _pointer_in_pane_bounds()
        # (shared with the hover status bar and painting gates) so this
        # pane's real bounds are used instead.
        if self.current_label is None or not self._pointer_in_pane_bounds(
            view, image_point
        ):
            self.status_bar.showMessage(
                "Outside the adjacent slice's image (Compare Adjacent)."
                if self._compare_mode_active and view is self.anno_view
                else "Outside DAPI image"
            )
            return
        # Compare Adjacent (2026-09-22 user request): a right-click on the
        # comparison pane picks from *that* adjacent slice's own
        # annotation, not the current slice's -- the pane is displaying
        # the adjacent slice's composite, so reading self.current_label
        # there would silently target the wrong slice's region id whenever
        # the two slices' labels differ at that pixel.
        adjacent_array = getattr(self, "_compare_adjacent_label_array", None)
        if (
            self._compare_mode_active
            and view is self.anno_view
            and adjacent_array is not None
        ):
            # The comparison picture is no longer scaled to fill
            # self.current_label's box (2026-09-23: native-size + padded/
            # centered canvas, see _enter_compare_mode()) -- this used to
            # assume a uniform fill and remap the click by a width/height
            # ratio, which silently picked the *wrong* pixel of
            # adjacent_array (sometimes one that happened to still be a
            # valid-looking id) once the picture was placed with an
            # offset instead of stretched to the canvas edges (2026-09-23
            # user report: a right-click on visibly empty padding still
            # "worked"). Undo the same offset _enter_compare_mode() used
            # to place the picture, then only remap into
            # adjacent_array's own coordinates if its resolution actually
            # differs from the picture's (annotation pickle vs. DAPI
            # preview can legitimately be sized differently -- see the
            # overlay-resize a few lines up in _enter_compare_mode()).
            label_value = self._compare_adjacent_label_at(image_point)
            if label_value is None:
                return
            region_name = self.structure_map.get(label_value, {}).get(
                "name", "Unknown region"
            )
            slice_id = self._compare_adjacent_slice_id or "adjacent slice"
            self.set_paint_region(label_value)
            self._sync_area_combo_to_region(label_value)
            self.repaint_selected_only()
            # Refresh the comparison picture's own selection highlight to
            # match (2026-09-23 user request) -- repaint_selected_only()
            # only repaints the current slice's Annotation pane item,
            # which this compare item sits on top of and hides.
            self._refresh_compare_if_active()
            self.status_bar.showMessage(
                f"Paint target set from {slice_id} (Compare Adjacent): "
                f"{region_name}"
            )
            return
        else:
            label_value = int(self.current_label[image_point.y(), image_point.x()])
        self.set_paint_region(label_value)
        self._sync_area_combo_to_region(label_value)
        self.repaint_selected_only()
        # A paint target picked from the DAPI pane (not the comparison
        # pane) should still update the comparison highlight if Compare
        # Adjacent happens to be on at the same time -- painting there
        # stays available while comparing (see the MouseButtonPress guard
        # in eventFilter()), and the highlight should track whichever
        # pane the selection actually came from.
        self._refresh_compare_if_active()

    def eventFilter(self, source, event):
        if (
            source is self.paint_dock
            and event.type() == QEvent.Type.Resize
            and self._options_width_ready
            and self.paint_dock.isVisible()
            and not self.paint_dock.isFloating()
            and self.paint_dock.width() > 0
        ):
            width = self.paint_dock.width()
            self._saved_options_dock_width = width
            self._options_settings.setValue("adjustment/optionsDockWidth", width)
        if source in (self.paint_dock, self) and event.type() == QEvent.Type.Resize:
            # 2026-09-23 user report: on the very first slice, zooming in
            # right after the viewer opens (before show_maximized_with_
            # default_options_width()'s deferred 0ms/200ms resizeDocks()
            # calls have actually settled the Options dock's final width --
            # see that method's own comment on why a second, later call is
            # needed on Windows) left the pan margins/scene rects locked to
            # the *pre*-settle viewport width. When that deferred resize
            # then landed (often coinciding with whatever the user did
            # next, e.g. pressing Next), the DAPI/Annotation panes'
            # scrollbar ranges changed size underneath the still-stale
            # cached margins -- and show_image_with_overlay()'s scrollbar-
            # value preserve/restore (see its own docstring) then re-applied
            # an old scrollbar *value* that no longer corresponded to the
            # same on-screen position under the new range, pushing the
            # image toward one edge instead of keeping it centred. Only an
            # image_splitter divider drag was wired to
            # _refresh_virtual_pan_scene_rects() (via splitterMoved); a dock
            # resize (this deferred settle, a user dragging the dock
            # divider, or the main window itself being resized/un-
            # maximized) never re-ran it at all. Routing both the Options
            # dock's and the main window's own Resize events through the
            # same debounced timer the splitter drag already uses closes
            # that gap generally, instead of special-casing the Next-button
            # timing that happened to be how the user first noticed it.
            self._splitter_refresh_timer.start(30)
        if isinstance(source, QLineEdit) and event.type() in (
            QEvent.Type.KeyPress, QEvent.Type.KeyRelease, QEvent.Type.ShortcutOverride
        ) and event.key() == Qt.Key.Key_Space:
            if event.type() == QEvent.Type.ShortcutOverride:
                event.accept()
            elif not event.isAutoRepeat():
                replacement = QKeyEvent(event.type(), Qt.Key.Key_Return, event.modifiers(), '\r')
                QApplication.sendEvent(source, replacement)
            return True
        # Region picker: clicking into the Search box or the editable Area
        # combo selects all of its text, like clicking a browser address
        # bar (2026-09-21 user request; fixed 2026-09-22 after user report
        # it wasn't firing). Qt grants focus as part of its own click
        # handling *before* the MouseButtonPress event reaches this
        # filter, so by the time the press arrives here hasFocus() is
        # already True even on the very click that brought focus in -- the
        # original `not source.hasFocus()` guard was therefore always
        # False and silently skipped every click. Tracking FocusIn/FocusOut
        # explicitly instead correctly distinguishes "this click brought
        # focus in" from "this click is on an already-focused field" (the
        # latter still just places the cursor normally, so a specific
        # character can still be edited). QTimer.singleShot(0, ...) still
        # defers the selection past this event's own default
        # click-to-position-cursor handling, which would otherwise
        # immediately collapse it back to a single point.
        if source in (self.area_search_box, self.area_combo.lineEdit()):
            if event.type() == QEvent.Type.FocusIn:
                self._region_picker_pending_select_all.add(source)
            elif event.type() == QEvent.Type.FocusOut:
                self._region_picker_pending_select_all.discard(source)
        if (
            source in (self.area_search_box, self.area_combo.lineEdit())
            and event.type() == QEvent.Type.MouseButtonPress
            and event.button() == Qt.MouseButton.LeftButton
            and source in self._region_picker_pending_select_all
        ):
            self._region_picker_pending_select_all.discard(source)
            QTimer.singleShot(0, source.selectAll)
        if source is self and event.type() == QEvent.Type.WindowDeactivate:
            self._is_panning = False
            self._pan_last_pos = None
            self._right_pan_pending = False
            self._right_pan_start_pos = None
            self._set_space_navigation(False)
        if source in (self.img_view, self.anno_view):
            if event.type() == QEvent.Type.FocusOut:
                self._is_panning = False
                self._pan_last_pos = None
                self._right_pan_pending = False
                self._right_pan_start_pos = None
                self._set_space_navigation(False)
            if (
                event.type() == QEvent.Type.KeyPress
                and event.key() == Qt.Key.Key_Space
                and not event.isAutoRepeat()
            ):
                self._set_space_navigation(True)
                return True
            elif (
                event.type() == QEvent.Type.KeyRelease
                and event.key() == Qt.Key.Key_Space
                and not event.isAutoRepeat()
            ):
                self._set_space_navigation(False)
                return True

        is_view_vp = source in (
            self.img_view.viewport(),
            self.anno_view.viewport(),
        )

        if is_view_vp and event.type() == QEvent.Type.Wheel:
            if self._view_shortcuts_allowed():
                delta = event.angleDelta().y()
                if delta != 0:
                    # Ctrl+scroll resizes the brush instead of zooming
                    # (2026-09-21 user request). Same per-notch magnitude
                    # as the '-'/'=' brush-size shortcuts (_nudge_brush's
                    # existing callers), mirroring how plain scroll's
                    # per-notch zoom step (10) already matches '['/']'.
                    if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
                        self._nudge_brush(1 if delta > 0 else -1)
                    else:
                        self._nudge_zoom(10 if delta > 0 else -10)
                    return True
            return True

        if event.type() == QEvent.Type.MouseButtonPress and is_view_vp:
            if event.button() == Qt.MouseButton.MiddleButton or (
                event.button() == Qt.MouseButton.LeftButton
                and getattr(self, "_space_down", False)
            ):
                self._is_panning = True
                self._space_pan_active = (
                    event.button() == Qt.MouseButton.LeftButton
                    and getattr(self, "_space_down", False)
                )
                self._pan_last_pos = event.pos()
                self.is_drawing = False
                self._sync_navigation_cursor()
                return True
            if (
                event.button() == Qt.MouseButton.LeftButton
                and self.allow_adjustment.isChecked()
                and self.selected_region_id is not None
                and not getattr(self, "_space_down", False)
            ):
                if (
                    self._compare_mode_active
                    and source is self.anno_view.viewport()
                ):
                    # The comparison pane is showing an adjacent
                    # slice's image, not the current annotation --
                    # painting there would silently land on the
                    # current slice's label array under the wrong
                    # picture. Edit via the DAPI pane instead (2026-09-22
                    # user request: current-slice editing stays
                    # available while comparing).
                    self.status_bar.showMessage(
                        "Comparison pane is read-only -- edit the "
                        "current slice from the DAPI pane instead."
                    )
                    return True
                point = event.pos()
                press_view = source.parent()
                image_point = self.view_to_image_coordinates(
                    press_view, point
                )
                if not self._pointer_in_pane_bounds(press_view, image_point):
                    self.is_drawing = False
                    self.last_draw_point = None
                    self._hide_brush_cursor()
                    self.status_bar.showMessage("Outside DAPI image")
                    return True
                self.is_drawing = True
                self.last_draw_point = image_point
                self.draw_on_image(image_point)
                return True
            if event.button() == Qt.MouseButton.RightButton:
                # A short right click selects a target; a right drag pans.
                self._right_pan_pending = True
                self._right_pan_start_pos = event.pos()
                return True

        elif event.type() == QEvent.Type.MouseMove and is_view_vp:
            point = event.pos()
            view = source.parent()
            if self._right_pan_pending:
                start = self._right_pan_start_pos
                if start is None or (
                    point - start
                ).manhattanLength() < QApplication.startDragDistance():
                    return True
                self._right_pan_pending = False
                self._right_pan_start_pos = None
                self._is_panning = True
                self._space_pan_active = False
                self._pan_last_pos = start
                self._sync_navigation_cursor()
            if self._is_panning and self._pan_last_pos is not None:
                dx = self._pan_last_pos.x() - point.x()
                dy = self._pan_last_pos.y() - point.y()
                self._pan_last_pos = point
                self._pan_by_pixels(dx, dy)
                return True
            if self.is_drawing:
                image_point = self.view_to_image_coordinates(view, point)
                if not self._pointer_in_pane_bounds(view, image_point):
                    # Do not connect an in-image capsule through the margin
                    # if the pointer later returns to the DAPI pixmap.
                    self.last_draw_point = None
                    self._hide_brush_cursor()
                    self.status_bar.showMessage("Outside DAPI image")
                    return True
                if self.last_draw_point is None:
                    self.last_draw_point = image_point
                    self.draw_on_image(image_point)
                    self._update_brush_cursor(view, image_point)
                    return True
                if image_point != self.last_draw_point:
                    # One vectorized round-cap capsule keeps fast drags
                    # continuous without Python-level stamp interpolation.
                    self._paint_capsule_segment(self.last_draw_point, image_point)
                    self.last_draw_point = image_point
                # Keep the brush ring visible/positioned while dragging, too.
                self._update_brush_cursor(view, image_point)
                return True
            image_point = self.view_to_image_coordinates(view, point)
            if not self._pointer_in_pane_bounds(view, image_point):
                # 2026-09-23 user report: hovering the part of a Compare
                # Adjacent reference picture past current_label's own box
                # showed "Outside DAPI image" instead of the reference
                # slice's info -- see _pointer_in_pane_bounds().
                self._hide_brush_cursor()
                self.status_bar.showMessage(
                    "Outside the adjacent slice's image (Compare Adjacent)."
                    if self._compare_mode_active and view is self.anno_view
                    else "Outside DAPI image"
                )
                return True
            self.update_status_bar_with_region(image_point, view)
            self._update_brush_cursor(view, image_point)
            return True

        elif event.type() == QEvent.Type.MouseButtonRelease and is_view_vp:
            if self._is_panning and event.button() in (
                Qt.MouseButton.MiddleButton,
                Qt.MouseButton.LeftButton,
                Qt.MouseButton.RightButton,
            ):
                self._is_panning = False
                self._space_pan_active = False
                self._pan_last_pos = None
                self._right_pan_pending = False
                self._right_pan_start_pos = None
                self._sync_navigation_cursor()
                return True
            if (
                event.button() == Qt.MouseButton.RightButton
                and self._right_pan_pending
            ):
                self._right_pan_pending = False
                self._right_pan_start_pos = None
                self._select_paint_target_at_view_pos(source.parent(), event.pos())
                return True
            if self.is_drawing and event.button() == Qt.MouseButton.LeftButton:
                self.is_drawing = False
                self.last_draw_point = None
                self.current_delta += 1
                self._update_parcellation_labels()
                # Local overlay refresh (stroke bbox only) instead of a full
                # re-color-map of the whole label array — much faster on release.
                self._finalize_last_stroke()
                self._stroke_seen = None
                return True

        return super(AnnotationViewer, self).eventFilter(source, event)


if __name__ == "__main__":
    import perf_log
    perf_log.perf_start_total("adjust")
    parser = argparse.ArgumentParser(
        description="Allow adjustment of region alignments"
    )
    parser.add_argument(
        "-a",
        "--annotations",
        help="annotation files path",
        default="",
    )
    parser.add_argument(
        "-i",
        "--images",
        help="images path",
        default="",
    )
    parser.add_argument(
        "-s",
        "--structures",
        help="structures map",
    )
    parser.add_argument(
        "--slice-list",
        default="",
        help="Optional JSON file with slice_ids to restrict pairing",
    )
    parser.add_argument(
        "--previews-dir",
        default="",
        help="Optional low-res preview directory (default: sibling _previews of images dir)",
    )
    args = parser.parse_args()
    print(2, flush=True)
    print("Viewing...", flush=True)

    set_viewer_exit_reason("done")

    def on_app_exit():
        if get_viewer_exit_reason() == "cancel":
            print("Viewer closed", flush=True)
        else:
            print("Done!", flush=True)

    images_path = Path(args.images.strip())
    annotations_path = Path(args.annotations.strip())
    structure_map_path = Path(args.structures.strip())
    structure_map = pickle.load(open(structure_map_path, "rb"))

    graph_path = structure_map_path.parent / "structure_graph.json"
    catalog = None
    if graph_path.is_file():
        catalog = load_catalog(graph_path)
    else:
        print(
            f"WARNING: structure graph not found at {graph_path}",
            file=sys.stderr,
            flush=True,
        )

    pairs, orphan_images, orphan_annos = build_adjust_pairs(
        images_path, annotations_path, args.slice_list.strip() or None
    )
    if orphan_images:
        print(
            f"Unpaired images ({len(orphan_images)}): "
            + ", ".join(orphan_images[:20])
            + ("..." if len(orphan_images) > 20 else ""),
            file=sys.stderr,
            flush=True,
        )
    if orphan_annos:
        print(
            f"Unpaired annotations ({len(orphan_annos)}): "
            + ", ".join(orphan_annos[:20])
            + ("..." if len(orphan_annos) > 20 else ""),
            file=sys.stderr,
            flush=True,
        )

    app = QApplication(sys.argv)

    if not pairs:
        QMessageBox.critical(
            None,
            "No matched pairs",
            "No image/annotation pairs matched by slice ID.\n"
            "Check that DAPI images and annotation PKLs share the same filename stem "
            "(e.g. M528_s027.tif and Annotation_M528_s027.pkl).",
        )
        sys.exit(1)

    app.aboutToQuit.connect(on_app_exit)

    previews_dir = None
    if args.previews_dir.strip():
        previews_dir = Path(args.previews_dir.strip())

    window = AnnotationViewer(
        pairs, structure_map, images_path, previews_dir, catalog
    )
    if catalog is None:
        QMessageBox.critical(
            window,
            "Atlas catalog missing",
            f"Could not load CCF ontology:\n{graph_path}\n\n"
            "Paint-region selection is disabled; right-click on existing labels still works.",
        )
    window.show_maximized_with_default_options_width()
    raise_and_activate(window)

    sys.exit(app.exec_())
