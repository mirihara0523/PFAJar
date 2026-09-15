import numpy as np
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
from qtpy.QtGui import QImage, QPixmap, QPainter, QColor, QPen, QBrush, QKeySequence, QTransform, QKeyEvent
from qtpy.QtCore import Qt, QPoint, QPointF, QEvent, QTimer, QRectF, QSettings
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
        self._syncing_scroll = False
        self._pan_scene_initialized = False
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
            max_workers=1, thread_name_prefix="MasonJarDapiSeam"
        )
        self._dapi_live_cache_tempdir = tempfile.TemporaryDirectory(
            prefix="masonjar-adjust-live-"
        )
        self._save_exit_flag = self.images_dir / ".adjust_save_exit"
        self._save_exit_timer = QTimer(self)
        self._save_exit_timer.timeout.connect(self._poll_save_exit)
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

        # A shared label column keeps all sliders aligned even as
        # Zoom/Brush text changes with their current values.
        for slider_label in (
            self.opacity_label,
            self.zoom_label,
            self.brush_label,
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
        # Brush ring cursor appearance (configurable in Brush & edits panel).
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
        options_inner = QWidget(self)
        options_layout = QVBoxLayout()
        options_layout.setContentsMargins(4, 4, 4, 4)
        self._init_paint_controls(options_layout)
        self._init_parcellation_controls(options_layout)
        options_inner.setLayout(options_layout)
        # Keep the full option stack as the scroll area's content size. Without
        # an explicit minimum height, a floating/narrow dock can compress the
        # child widget and Qt incorrectly concludes that no vertical overflow
        # exists, leaving the Display section clipped with no scrollbar.
        options_inner.setMinimumHeight(options_layout.sizeHint().height())
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
        self._options_settings = QSettings("MasonJar", "MasonJar")
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
        self.seam_channel_toggle = QPushButton("Seam correction", self)
        self.seam_channel_toggle.setCheckable(True)
        self.seam_channel_toggle.setToolTip(
            "Show a live seam-corrected DAPI/channel image."
        )
        self.seam_channel_toggle.setEnabled(False)
        self.seam_channel_toggle.toggled.connect(self._on_seam_channel_toggled)
        self.seam_mode_label = QLabel("Known-geometry", self)
        self.seam_mode_label.setToolTip(
            "Correction mode for the current slice: imported seamgrid when "
            "available, otherwise grid-estimated."
        )
        self.seam_mode_label.setStyleSheet("color: #6c7a89; padding: 0 4px;")
        self.seam_mode_label.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        # Reserve the mode line even while Seam correction is off.  Hiding a
        # layout child changes the toolbar's height and visibly moves its
        # button when Known-geometry/Grid-estimated first appears.
        seam_mode_height = self.seam_mode_label.sizeHint().height()
        self.seam_mode_label.setFixedHeight(seam_mode_height)
        self.seam_mode_label.setText("")
        seam_control = QWidget(self)
        seam_layout = QVBoxLayout(seam_control)
        seam_layout.setContentsMargins(0, 0, 0, 0)
        seam_layout.setSpacing(0)
        # The lower mode line makes this control taller than its neighbours.
        # Reserve an equal upper line so the button itself stays on the same
        # vertical centreline as Channel, Previous, and the other toolbar
        # buttons whether the label has text or is blank.
        seam_layout.addSpacing(seam_mode_height)
        seam_layout.addWidget(self.seam_channel_toggle)
        seam_layout.addWidget(self.seam_mode_label)
        header_toolbar.addWidget(seam_control)

        self.swap_views_button = QPushButton("Swap Map/DAPI", self)
        self.swap_views_button.setToolTip(
            "Swap the left/right positions of the DAPI image and annotation map."
        )
        self.swap_views_button.clicked.connect(self._swap_views)
        header_toolbar.addWidget(self.swap_views_button)
        header_toolbar.addSeparator()

        self.overlay_toggle = QPushButton("Toggle Overlay", self)
        self.overlay_toggle.setCheckable(True)
        self.overlay_toggle.setChecked(self.overlay_visible)
        self.overlay_toggle.setToolTip(
            "Show or hide the colored annotation overlay on the DAPI image."
        )
        self.overlay_toggle.toggled.connect(self.toggle_overlay)
        header_toolbar.addWidget(self.overlay_toggle)

        self.allow_adjustment = QPushButton("Allow Adjustment", self)
        self.allow_adjustment.setCheckable(True)
        self.allow_adjustment.setChecked(False)
        self.allow_adjustment.toggled.connect(
            lambda _checked: self._update_paint_target_strip()
        )
        header_toolbar.addWidget(self.allow_adjustment)
        header_toolbar.addSeparator()

        self.paint_dock_button = QPushButton("Options", self)
        self.paint_dock_button.setCheckable(True)
        self.paint_dock_button.setChecked(True)
        self.paint_dock_button.clicked.connect(self._toggle_paint_dock)
        header_toolbar.addWidget(self.paint_dock_button)
        self.paint_dock.visibilityChanged.connect(self._on_paint_dock_visibility)

        self.annotation_map_toggle = QPushButton("Annotation map", self)
        self.annotation_map_toggle.setCheckable(True)
        self.annotation_map_toggle.setChecked(True)
        self.annotation_map_toggle.setToolTip(
            "Show or hide the annotation map. When hidden, the DAPI view uses "
            "the available central workspace."
        )
        self.annotation_map_toggle.toggled.connect(self._on_annotation_map_toggled)
        header_toolbar.addWidget(self.annotation_map_toggle)

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
        self.anno_view.setVisible(bool(visible))
        if visible:
            # The requested restore policy is 5:5 after the map has been
            # hidden; otherwise the splitter keeps the user's drag position.
            QTimer.singleShot(0, self._reset_image_splitter_equal)
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
        """Keep both viewports on one scene point after a divider drag."""
        QTimer.singleShot(0, self._refresh_virtual_pan_scene_rects)

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
        self.channel_combo.setEnabled(True)
        for i, (name, path) in enumerate(self.channel_sources):
            self.channel_combo.addItem(name, str(path))
            if name in ("DAPI", "DAPI (pipeline)", "Dapi"):
                default_index = i

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

    def _update_seam_mode_label(self, path) -> None:
        """Show the live mode that the current slice will use.

        A Process output is intentionally not consulted here: Adjustment
        Viewer always computes its own live result so this label and the
        displayed correction share one mode decision.
        """
        label = getattr(self, "seam_mode_label", None)
        if label is None:
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
        toggle = getattr(self, "seam_channel_toggle", None)
        label.setText(
            mode_label if toggle is not None and toggle.isChecked() else ""
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
        else:
            rid = int(self.selected_region_id)
            r, g, b = resolve_label_color(rid, self.structure_map, self.catalog)
            self.paint_swatch.setStyleSheet(
                f"background-color: rgb({r}, {g}, {b});"
            )
            self.paint_target_name.setText(self.selected_region_name)
            self.paint_target_name.setToolTip(self._region_tooltip(rid))

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

    def _lock_scene_rect_to_pixmap(self, scene: QGraphicsScene, pixmap: QPixmap):
        """Set a centred virtual canvas so fit-to-window images can pan.

        A scene rect equal to a small pixmap has no scrollbar range and Qt's
        AlignCenter pins the image in place.  Equal virtual margins retain the
        centred starting position while giving both axes enough scene space for
        drag-pan at every zoom level.
        """
        if pixmap is None or pixmap.isNull():
            return
        view = self.img_view if scene is self.img_scene else self.anno_view
        scale = max(self.zoom_level / 100.0, 0.01)
        viewport = view.viewport().size()
        margin_x = max(64.0, viewport.width() / scale)
        margin_y = max(64.0, viewport.height() / scale)
        scene.setSceneRect(
            QRectF(
                -margin_x,
                -margin_y,
                pixmap.width() + 2 * margin_x,
                pixmap.height() + 2 * margin_y,
            )
        )

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
        """Resize virtual pan margins after the draggable splitter moves."""
        if self.img_pixmap is None or self.img_pixmap.isNull():
            return
        center = self._viewport_center_scene_pos(self.img_view)
        self._lock_scene_rect_to_pixmap(self.img_scene, self.img_pixmap)
        anno_pixmap = self._anno_pixmap()
        if anno_pixmap is not None and not anno_pixmap.isNull():
            self._lock_scene_rect_to_pixmap(self.anno_scene, anno_pixmap)
        self._center_linked_views(center)

    def _viewport_center_scene_pos(self, view: QGraphicsView) -> QPointF:
        vp = view.viewport()
        return view.mapToScene(vp.rect().center())

    def _apply_zoom(self, zoom_percent: int, *, center_scene_pos: QPointF | None = None):
        """Scale both panes, keeping the scene point under the viewport center."""
        zoom_percent = max(50, min(1000, int(zoom_percent)))
        if center_scene_pos is None:
            center_scene_pos = self._viewport_center_scene_pos(self.img_view)
        scale = zoom_percent / 100.0
        transform = QTransform()
        transform.scale(scale, scale)
        self._syncing_scroll = True
        try:
            for view in (self.img_view, self.anno_view):
                view.setTransform(transform)
        finally:
            self._syncing_scroll = False
        self._center_linked_views(center_scene_pos)
        self.zoom_level = zoom_percent
        self.zoom_label.setText(f"Zoom {self.zoom_level}%")
        if self.zoom_slider.value() != zoom_percent:
            self.zoom_slider.blockSignals(True)
            self.zoom_slider.setValue(zoom_percent)
            self.zoom_slider.blockSignals(False)

    def _sync_scroll_from(self, source: QGraphicsView):
        if self._syncing_scroll:
            return
        self._center_linked_views(self._viewport_center_scene_pos(source))

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
            hay = f"{node.get('acronym', '')} {node.get('name', '')}".lower()
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
            hay = f"{node.get('acronym', '')} {node.get('name', '')}".lower()
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
            self.paint_resolution_warning.setVisible(True)
        else:
            self.paint_resolution_warning.clear()
            self.paint_resolution_warning.setVisible(False)

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
        return f"{node['acronym']} — {node['name']}"

    def _region_picker_text(self, node: dict) -> str:
        """Compact Area text; the full region name is supplied as a tooltip."""
        return str(node.get("acronym") or node.get("name") or "Unknown region")

    @staticmethod
    def _show_current_combo_item_tooltip(combo: QComboBox):
        """Expose an item's own tooltip when hovering the closed combo box."""
        tooltip = combo.itemData(combo.currentIndex(), Qt.ItemDataRole.ToolTipRole)
        combo.setToolTip(str(tooltip or ""))

    def _region_tooltip(self, region_id: int) -> str:
        node = get_region(int(region_id), self.catalog) if self.catalog else None
        parts = [str(node["name"])] if node and node.get("name") else []
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

    def _make_group_collapsible(self, group):
        """Add a clickable header that expands/collapses the group's contents.
        Uses a QToolButton (not QGroupBox.setCheckable) so it never toggles child
        enabled state, which would clash with app-managed disables (e.g. catalog)."""
        layout = group.layout()
        if layout is None:
            return
        title = group.title()
        group.setTitle("")
        header = QToolButton(group)
        header.setText("▾ " + title)
        header.setCheckable(True)
        header.setChecked(True)
        header.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        header.setStyleSheet(
            "QToolButton { border: none; font-weight: bold; padding: 2px; }"
        )
        header.setCursor(Qt.CursorShape.PointingHandCursor)
        layout.insertWidget(0, header)
        header.toggled.connect(
            lambda on, g=group, h=header, t=title: self._set_group_collapsed(
                g, h, t, on
            )
        )

    def _set_group_collapsed(self, group, header, title, expanded):
        header.setText(("▾ " if expanded else "▸ ") + title)
        layout = group.layout()
        if layout is None:
            return
        for i in range(layout.count()):
            item = layout.itemAt(i)
            widget = item.widget()
            if widget is header:
                continue
            if widget is not None:
                widget.setVisible(expanded)
                continue
            sub = item.layout()
            if sub is not None:
                for j in range(sub.count()):
                    sub_widget = sub.itemAt(j).widget()
                    if sub_widget is not None:
                        sub_widget.setVisible(expanded)

    def _init_paint_controls(self, ui_layout):
        """Region picker, paint target, view sliders, and brush/edit controls."""
        region_group = QGroupBox("Region picker", self)
        region_layout = QVBoxLayout()

        # Labels above their fields let every picker box share the group's left
        # edge, including the disabled CCFv3 Level control.
        region_layout.addWidget(QLabel("Search:", self))
        self.area_search_box.setMinimumWidth(80)
        region_layout.addWidget(self.area_search_box)

        region_layout.addWidget(QLabel("Tier:", self))
        region_layout.addWidget(self.tier_combo)

        region_layout.addWidget(QLabel("Level:", self))
        region_layout.addWidget(self.level_combo)

        region_layout.addWidget(QLabel("Area:", self))
        region_layout.addWidget(self.area_combo)

        region_layout.addWidget(self.ccf_advanced_toggle)
        self.paint_resolution_warning = QLabel("", self)
        self.paint_resolution_warning.setWordWrap(True)
        self.paint_resolution_warning.setStyleSheet("color: #856404;")
        self.paint_resolution_warning.setVisible(False)
        region_layout.addWidget(self.paint_resolution_warning)
        region_group.setLayout(region_layout)
        self._make_group_collapsible(region_group)
        ui_layout.addWidget(region_group)

        target_group = QGroupBox("Paint target", self)
        target_layout = QVBoxLayout()
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
        target_group.setLayout(target_layout)
        self._make_group_collapsible(target_group)
        ui_layout.addWidget(target_group)

        view_group = QGroupBox("View", self)
        view_layout = QVBoxLayout()
        opacity_row = QHBoxLayout()
        opacity_row.addWidget(self.opacity_label)
        opacity_row.addWidget(self.opacity_slider, 1)
        view_layout.addLayout(opacity_row)
        zoom_row = QHBoxLayout()
        zoom_row.addWidget(self.zoom_label)
        zoom_row.addWidget(self.zoom_slider, 1)
        view_layout.addLayout(zoom_row)
        view_group.setLayout(view_layout)
        self._make_group_collapsible(view_group)
        ui_layout.addWidget(view_group)

        brush_group = QGroupBox("Brush & edits", self)
        brush_layout = QVBoxLayout()
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
        ui_layout.addWidget(brush_group)

        self._paint_controls_group = region_group

    def _init_parcellation_controls(self, ui_layout):
        """Parcellation level controls (separate from paint-brush hierarchy)."""
        group = QGroupBox("Parcellation", self)
        layout = QVBoxLayout()

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
        self._make_group_collapsible(group)
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

    def switch_channel(self, path, display_name, *, display_path=None):
        """Load a low-res background image and refresh the annotation overlay."""
        path = Path(path)
        self.active_channel_path = path
        # A session-cached live seam image has a temporary hashed filename.
        # Retain the original channel path for the user-facing Background line.
        self.active_channel_display_path = Path(display_path) if display_path else path
        self.active_channel_name = display_name
        with perf_log.perf_section("adjust.channel.load_image"):
            self.img_pixmap = QPixmap(str(path))
        self.img_pixmap = self.img_pixmap.scaled(
            self.current_label.shape[1],
            self.current_label.shape[0],
            Qt.AspectRatioMode.KeepAspectRatio,
        )
        self._set_img_pixmap(self.img_pixmap)
        self._update_section_labels()
        self.show_image_with_overlay()

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

    def _pan_views(self, fx: float, fy: float):
        """Pan both panes by ~20% of the visible viewport."""
        if not self._view_shortcuts_allowed():
            return
        vp = self.img_view.viewport()
        dx = int(round(fx * 0.2 * vp.width()))
        dy = int(round(fy * 0.2 * vp.height()))
        self._pan_by_pixels(dx, dy)

    def _pan_by_pixels(self, dx: int, dy: int):
        """Pan both panes by a shared scene-coordinate delta.

        This does not depend on equal scrollbar ranges, so it also works for
        a fit-to-window DAPI image and after the user changes splitter ratio.
        """
        if dx == 0 and dy == 0:
            return
        scale = max(self.zoom_level / 100.0, 0.01)
        center = self._viewport_center_scene_pos(self.img_view)
        center = center + QPointF(dx / scale, dy / scale)
        self._center_linked_views(center)

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
            for key in keys:
                sc = QShortcut(QKeySequence(key), self)
                sc.setContext(Qt.ShortcutContext.WindowShortcut)
                sc.activated.connect(slot)

        bind(["-", "Minus", "KeypadMinus"], lambda: self._nudge_zoom(-10))
        bind(["=", "+", "Plus", "KeypadPlus"], lambda: self._nudge_zoom(10))
        bind(["Left"], lambda: self._pan_views(-1, 0))
        bind(["Right"], lambda: self._pan_views(1, 0))
        bind(["Up"], lambda: self._pan_views(0, -1))
        bind(["Down"], lambda: self._pan_views(0, 1))
        bind(["Delete", "Backspace"], self._fill_selected_label_with_liw)

    def update_brush(self):
        self.brush_size = self.brush_slider.value()
        self._update_paint_target_strip()

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

    def show_image_with_overlay(self):
        center = self._viewport_center_scene_pos(self.img_view)
        label_array = np.array(self._label_for_display(), dtype=np.uint32)
        # Build the colored overlay by mapping label -> color with a vectorized
        # lookup (searchsorted + gather) instead of a per-label / per-pixel
        # QPainter.drawPoints loop, which was O(pixels x labels) in Python.
        # Byte order matches Qt RGB32 (B, G, R, A) so the downstream
        # qimage/add_outlines pipeline is unchanged.
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
            anno_as_array = np.ascontiguousarray(table[idx])
        with perf_log.perf_section("adjust.overlay.outlines"):
            anno_as_array = add_outlines(label_array, anno_as_array)
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
        self._apply_zoom(self.zoom_level, center_scene_pos=center)

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
        _, anno_path, _ = self.pairs[self.current_index]
        with open(anno_path, "wb") as f:
            pickle.dump(self.current_label, f)
        self.was_changed = False
        self._update_parcellation_labels()
        self._refresh_annotation_label_audit_cache(show_intensity_notice=True)

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
        self._cancel_dapi_prefetch()
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
        with perf_log.perf_section("adjust.nav.render"):
            self.show_image_with_overlay()

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

    def update_status_bar_with_region(self, pos):
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
        """Repaint the selected region only"""
        if (
            not self._overlay_ready
            or not hasattr(self, "anno_pixmap")
            or self.anno_pixmap is None
            or self.anno_pixmap.isNull()
        ):
            return

        # make a copy of the annotation pixmap
        anno_pixmap = self._anno_pixmap()
        if anno_pixmap is None:
            return
        painter = QPainter(anno_pixmap)
        color = QColor(218, 112, 214)
        painter.setPen(color)

        # Create a mask where the label array matches the current label value
        mask = self.current_label == self.selected_region_id
        points = [QPoint(j, i) for i, j in zip(*np.where(mask))]
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

    def _select_paint_target_at_view_pos(self, view, point):
        """Select the atlas label at a click that did not become a right-drag."""
        image_point = self.view_to_image_coordinates(view, point)
        if (
            self.current_label is None
            or image_point.x() < 0
            or image_point.y() < 0
            or image_point.x() >= self.current_label.shape[1]
            or image_point.y() >= self.current_label.shape[0]
            or not self._is_inside_dapi_image(image_point)
        ):
            self.status_bar.showMessage("Outside DAPI image")
            return
        label_value = int(self.current_label[image_point.y(), image_point.x()])
        self.set_paint_region(label_value)
        self._sync_area_combo_to_region(label_value)
        self.repaint_selected_only()

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
        if isinstance(source, QLineEdit) and event.type() in (
            QEvent.Type.KeyPress, QEvent.Type.KeyRelease, QEvent.Type.ShortcutOverride
        ) and event.key() == Qt.Key.Key_Space:
            if event.type() == QEvent.Type.ShortcutOverride:
                event.accept()
            elif not event.isAutoRepeat():
                replacement = QKeyEvent(event.type(), Qt.Key.Key_Return, event.modifiers(), '\r')
                QApplication.sendEvent(source, replacement)
            return True
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
                point = event.pos()
                image_point = self.view_to_image_coordinates(
                    source.parent(), point
                )
                if not self._is_inside_dapi_image(image_point):
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
                if not self._is_inside_dapi_image(image_point):
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
            if not self._is_inside_dapi_image(image_point):
                self._hide_brush_cursor()
                self.status_bar.showMessage("Outside DAPI image")
                return True
            self.update_status_bar_with_region(image_point)
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
