"""Regression: paint-region combo must not repaint before annotation overlay exists."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pickle
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest
from qtpy.QtWidgets import QApplication

_REPO_ROOT = Path(__file__).resolve().parents[2]
_PY_DIR = _REPO_ROOT / "py"
if str(_PY_DIR) not in sys.path:
    sys.path.insert(0, str(_PY_DIR))


@pytest.fixture(scope="module")
def viewer_class():
    structure_catalog = MagicMock(
        list_levels=MagicMock(return_value=[]),
        list_regions_at_level=MagicMock(return_value=[]),
        list_tiers=MagicMock(return_value=[]),
        list_ccf_levels=MagicMock(return_value=[]),
        format_ccf_level_label=MagicMock(return_value=""),
        get_region=MagicMock(return_value=None),
        load_catalog=MagicMock(),
        CCF_ADVANCED_HELP="",
    )
    with patch.dict(
        sys.modules,
        {
            "slice_atlas": MagicMock(add_outlines=lambda a, b: b),
            "adjust_channels": MagicMock(
                build_lowres_channel_index=MagicMock(return_value={}),
                lowres_channels_for_slice=MagicMock(return_value=[]),
                resolve_previews_dir=MagicMock(return_value=Path("/tmp")),
            ),
            "slice_index": MagicMock(build_adjust_pairs=MagicMock()),
            "structure_catalog": structure_catalog,
        },
    ):
        import adjust

        viewer_cls = adjust.AnnotationViewer
    sys.modules["adjust"] = adjust
    return viewer_cls


def test_repaint_selected_only_noops_before_overlay_ready(viewer_class):
    viewer = viewer_class.__new__(viewer_class)
    viewer._overlay_ready = False
    viewer.anno_pixmap = None
    viewer.selected_region_id = np.uint32(315)
    viewer.current_label = np.zeros((4, 4), dtype=np.uint32)
    viewer.anno_scene = MagicMock()
    viewer.anno_scene.items.return_value = []
    viewer.img_scene = MagicMock()
    viewer.overlay_visible = False
    # Should not raise when anno_pixmap is missing
    viewer.repaint_selected_only()


def test_adjust_defers_repaint_until_overlay_flag():
    """Source guard: set_paint_region must not repaint before show_image_with_overlay."""
    src = (_PY_DIR / "adjust.py").read_text(encoding="utf-8")
    assert "self._overlay_ready = False" in src
    assert "if self._overlay_ready:" in src
    assert "self._overlay_ready = True" in src
    assert "from qt_image_utils import numpy_array_to_qimage" in src
    assert (_PY_DIR / "structure_catalog.py").is_file()


def test_init_paint_region_controls_after_paint_swatch():
    """Paint-target widgets must exist before hierarchy/area combo population."""
    src = (_PY_DIR / "adjust.py").read_text(encoding="utf-8")
    marker = "def initUI(self):\n        self.section_info_label"
    idx = src.find(marker)
    assert idx != -1
    init_ui = src[idx:].split("\n    def ")[0]
    swatch_pos = init_ui.find("self.paint_swatch")
    init_paint_pos = init_ui.find("self._init_paint_region_controls()")
    assert swatch_pos != -1 and init_paint_pos != -1
    assert swatch_pos < init_paint_pos
    assert 'if not hasattr(self, "paint_swatch"):' in src


def _init_ui_source():
    src = (_PY_DIR / "adjust.py").read_text(encoding="utf-8")
    marker = "def initUI(self):\n        self.section_info_label"
    idx = src.find(marker)
    assert idx != -1
    return src[idx:].split("\n    def ")[0]


def test_rebuild_channel_combo_after_widgets_in_initui():
    """Background channel combo must exist before rebuild_channel_combo runs."""
    init_ui = _init_ui_source()
    combo_pos = init_ui.find("self.channel_combo = QComboBox")
    status_pos = init_ui.find("self.setStatusBar(self.status_bar)")
    rebuild_pos = init_ui.find("self.rebuild_channel_combo()")
    assert combo_pos != -1 and status_pos != -1 and rebuild_pos != -1
    assert combo_pos < rebuild_pos
    assert status_pos < rebuild_pos
    assert "self.rebuild_channel_combo()" not in init_ui[:combo_pos]


def test_rebuild_channel_combo_has_channel_combo_guard():
    src = (_PY_DIR / "adjust.py").read_text(encoding="utf-8")
    assert 'self.__dict__.get("channel_combo")' in src
    assert "getattr(self, \"status_bar\", None)" in src


def test_rebuild_channel_combo_guard_without_widgets(viewer_class):
    viewer = viewer_class.__new__(viewer_class)
    viewer.pairs = [("/tmp/img.png", "/tmp/anno.pkl", "slice_a")]
    viewer.current_index = 0
    assert viewer.rebuild_channel_combo() is False


@pytest.fixture
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    yield app


def test_initui_completes_with_empty_channels(tmp_path, viewer_class, qapp):
    """initUI must not crash when no preview channels exist for the slice."""
    label = np.zeros((8, 8), dtype=np.uint32)
    anno_path = tmp_path / "Annotation_test.pkl"
    with open(anno_path, "wb") as f:
        pickle.dump(label, f)
    img_path = tmp_path / "test.png"
    pairs = [(str(img_path), str(anno_path), "test_slice")]

    with patch.object(sys.modules["adjust"], "ensure_full_backup"):
        viewer = viewer_class(pairs, structure_map={}, catalog=None)

    assert hasattr(viewer, "channel_combo")
    assert hasattr(viewer, "status_bar")
    assert hasattr(viewer, "paint_dock")
    assert hasattr(viewer, "paint_dock_button")
    assert viewer.channel_sources == []
    assert viewer.channel_combo.isEnabled() is False


def test_paint_controls_use_dock_not_top_toolbars():
    """Paint UI lives in the unified Options dock, not top toolbars."""
    src = (_PY_DIR / "adjust.py").read_text(encoding="utf-8")
    assert 'QDockWidget("Options"' in src
    assert 'setObjectName("OptionsDock")' in src
    assert "def _init_paint_controls(self, ui_layout):" in src
    assert 'QToolBar("Paint"' not in src
    assert 'QToolBar("Target"' not in src
    assert 'QToolBar("Controls"' not in src


def test_live_seam_prefetch_is_limited_to_dapi(viewer_class):
    """Adjacent live preparation must never cache signal-channel corrections."""
    assert viewer_class._is_dapi_channel("DAPI")
    assert viewer_class._is_dapi_channel("DAPI (pipeline)")
    assert not viewer_class._is_dapi_channel("Somata")
    assert not viewer_class._is_dapi_channel("Nuclei")

    src = (_PY_DIR / "adjust.py").read_text(encoding="utf-8")
    assert "def _schedule_adjacent_dapi_prefetch(self)" in src
    assert "if is_dapi:\n            self._schedule_adjacent_dapi_prefetch()" in src


def test_live_seam_cache_keeps_original_background_filename():
    """A temporary cache filename must never replace the Background filename."""
    src = (_PY_DIR / "adjust.py").read_text(encoding="utf-8")
    assert "self.switch_channel(live_path, name, display_path=path)" in src
    assert "self.active_channel_display_path = Path(display_path) if display_path else path" in src
    assert "self.active_channel_display_path.name" in src


def test_revisited_dapi_skips_the_original_image_load():
    """A cached DAPI live result is selected before switch_channel(path, name)."""
    src = (_PY_DIR / "adjust.py").read_text(encoding="utf-8")
    assert "cached_live = (" in src
    assert "self.switch_channel(cached_live, name, display_path=path)" in src
    assert "def _live_seam_context_for(self, path: Path, seam_correct, seam_slice_id: str):" in src
    assert "LOG: seam_live_context_cache_hit" in src


def test_completed_prefetch_is_kept_across_navigation_cancel():
    """A finished adjacent DAPI result must survive queue cancellation."""
    src = (_PY_DIR / "adjust.py").read_text(encoding="utf-8")
    assert "def _harvest_completed_dapi_prefetch(self) -> bool:" in src
    cancel = src.split("def _cancel_dapi_prefetch(self) -> None:", 1)[1].split(
        "def _dapi_channel_for_pair", 1
    )[0]
    assert "self._harvest_completed_dapi_prefetch()" in cancel


def test_live_seam_results_are_unique_per_channel_path():
    """Signal correction must not overwrite the cached DAPI result for a slice."""
    src = (_PY_DIR / "adjust.py").read_text(encoding="utf-8")
    compute = src.split("def _compute_seam_live(self, path: Path)", 1)[1].split(
        "@staticmethod", 1
    )[0]
    assert "channel_tag = hashlib.sha256(" in compute
    assert 'f"{self._current_slice_id()}_{channel_tag}.png"' in compute


def test_paint_target_keeps_hierarchy_context_beside_area_name():
    """The selected tier/level is part of the one-line paint target summary."""
    src = (_PY_DIR / "adjust.py").read_text(encoding="utf-8")
    target = src.split('target_group = QGroupBox("Paint target", self)', 1)[1].split(
        "target_group.setLayout(target_layout)", 1
    )[0]
    assert "target_top.addWidget(self.paint_target_name, 1)" in target
    assert "target_top.addWidget(\n            self.paint_tier_context," in target
    assert "target_layout.addWidget(\n            self.paint_tier_context," not in target


def test_region_picker_offers_full_detail_tier():
    """Direct painting can expose all CCF structures, not only semantic tiers."""
    src = (_PY_DIR / "adjust.py").read_text(encoding="utf-8")
    controls = src.split("def _init_paint_region_controls(self):", 1)[1].split(
        "def _current_catalog_level", 1
    )[0]
    assert 'self.tier_combo.addItem("Full detail", FULL_DETAIL_TIER)' in controls
    current_regions = src.split("def _current_regions(", 1)[1].split(
        "def _rebuild_area_combo", 1
    )[0]
    assert "if tier_id == FULL_DETAIL_TIER:" in current_regions
    assert 'regions = list(self.catalog.get("nodes") or [])' in current_regions


def test_parcellation_action_rows_expand_like_brush_edit_actions():
    """Parcellation action rows should fill the Options dock as it resizes."""
    src = (_PY_DIR / "adjust.py").read_text(encoding="utf-8")
    parcel = src.split("def _init_parcellation_controls(self, ui_layout):", 1)[1].split(
        "def _parcel_excluded_region_ids", 1
    )[0]
    assert "for button in (\n            self.parcel_apply_button," in parcel
    assert "self.parcel_apply_all_button," in parcel
    assert "for button in (\n            self.parcel_exclude_button," in parcel
    assert "self.parcel_clear_exclude_button," in parcel
    assert parcel.count("QSizePolicy.Policy.Expanding") >= 2


def test_annotation_map_toggle_releases_central_space_to_dapi():
    """The toolbar toggle must hide only the map pane, preserving DAPI view."""
    src = (_PY_DIR / "adjust.py").read_text(encoding="utf-8")
    assert 'self.annotation_map_toggle = QPushButton("Annotation map", self)' in src
    assert "self.annotation_map_toggle.setCheckable(True)" in src
    assert "header_toolbar.addWidget(self.annotation_map_toggle)" in src
    handler = src.split("def _on_annotation_map_toggled(self, visible: bool):", 1)[1].split(
        "def _toggle_paint_dock", 1
    )[0]
    assert "self.anno_view.setVisible(bool(visible))" in handler
    assert "self.img_view.viewport().update" in handler


def test_header_boolean_controls_use_options_style_toggle_buttons():
    """Header boolean controls use checkable buttons, never checkbox widgets."""
    src = (_PY_DIR / "adjust.py").read_text(encoding="utf-8")
    for name, text in (
        ("seam_channel_toggle", "Seam correction"),
        ("allow_adjustment", "Allow Adjustment"),
        ("annotation_map_toggle", "Annotation map"),
    ):
        assert f'self.{name} = QPushButton("{text}", self)' in src
        assert f"self.{name}.setCheckable(True)" in src

    assert 'self.overlay_toggle = QPushButton("Toggle Overlay", self)' in src
    assert "self.overlay_toggle.setCheckable(True)" in src
    assert "self.overlay_toggle.toggled.connect(self.toggle_overlay)" in src


def test_seam_mode_is_shown_below_active_seam_button_only():
    """Known/grid mode belongs beneath Seam correction and hides when off."""
    src = (_PY_DIR / "adjust.py").read_text(encoding="utf-8")
    header = src.split("self.seam_channel_toggle = QPushButton", 1)[1].split(
        "self.prev_button", 1
    )[0]
    assert "seam_layout.addWidget(self.seam_channel_toggle)" in header
    assert "seam_layout.addWidget(self.seam_mode_label)" in header
    assert "seam_layout.addSpacing(seam_mode_height)" in header
    assert "self.seam_mode_label.setFixedHeight(" in header
    assert 'self.seam_mode_label.setText("")' in header
    mode_update = src.split("def _update_seam_mode_label(self, path) -> None:", 1)[1].split(
        "def _apply_seam_correction", 1
    )[0]
    assert "mode_label if toggle is not None and toggle.isChecked() else \"\"" in mode_update
    toggled = src.split("def _on_seam_channel_toggled(self, checked: bool):", 1)[1].split(
        "def _on_channel_combo_changed", 1
    )[0]
    assert "self._update_seam_mode_label(path)" in toggled


def test_completed_prefetch_replaces_current_raw_dapi_preview():
    """An in-flight adjacent DAPI result must apply immediately on completion."""
    src = (_PY_DIR / "adjust.py").read_text(encoding="utf-8")
    harvest = src.split("def _harvest_completed_dapi_prefetch(self) -> bool:", 1)[1].split(
        "def _apply_harvested_dapi_if_current", 1
    )[0]
    assert "self._apply_harvested_dapi_if_current(key, Path(result))" in harvest
    apply_current = src.split("def _apply_harvested_dapi_if_current(", 1)[1].split(
        "def _cancel_dapi_prefetch", 1
    )[0]
    assert "self.switch_channel(result, name, display_path=source_path)" in apply_current
    assert "LOG: seam_live_prefetch_applied" in apply_current
    seam_apply = src.split("def _apply_seam_correction", 1)[1].split(
        "def _on_seam_channel_toggled", 1
    )[0]
    assert "LOG: seam_live_prefetch_wait" in seam_apply


def test_pan_uses_shared_scene_coordinates_when_scrollbars_differ():
    """Fit-to-window and splitter-resized panes pan from one scene centre."""
    src = (_PY_DIR / "adjust.py").read_text(encoding="utf-8")
    pan = src.split("def _pan_by_pixels(self, dx: int, dy: int):", 1)[1].split(
        "def keyPressEvent", 1
    )[0]
    assert "center = self._viewport_center_scene_pos(self.img_view)" in pan
    assert "center = center + QPointF(dx / scale, dy / scale)" in pan
    assert "self._center_linked_views(center)" in pan
    sync = src.split("def _sync_scroll_from(self, source: QGraphicsView):", 1)[1].split(
        "def _set_img_pixmap", 1
    )[0]
    assert "self._center_linked_views(self._viewport_center_scene_pos(source))" in sync


def test_header_places_swap_after_seam_and_navigation_at_far_right():
    """Swap follows Seam correction; spacer pushes section navigation to the end."""
    src = (_PY_DIR / "adjust.py").read_text(encoding="utf-8")
    header = src.split("header_toolbar = QToolBar(\"Header\", self)", 1)[1].split(
        "# Status bar", 1
    )[0]
    seam_pos = header.index("header_toolbar.addWidget(seam_control)")
    swap_pos = header.index("header_toolbar.addWidget(self.swap_views_button)")
    spacer_pos = header.index("header_toolbar.addWidget(header_spacer)")
    prev_pos = header.index("header_toolbar.addWidget(self.prev_button)")
    next_pos = header.index("header_toolbar.addWidget(self.next_button)")
    goto_pos = header.index("header_toolbar.addWidget(self.goto_button)")
    margin_pos = header.index("header_toolbar.addWidget(header_right_margin)")
    assert seam_pos < swap_pos < spacer_pos < prev_pos < next_pos < goto_pos < margin_pos
    assert "header_right_margin.setFixedWidth(20)" in header


def test_fit_to_window_pan_uses_a_virtual_scene_margin():
    """A centred small preview has scene space for pan on both axes."""
    src = (_PY_DIR / "adjust.py").read_text(encoding="utf-8")
    scene_rect = src.split("def _lock_scene_rect_to_pixmap", 1)[1].split(
        "def _viewport_center_scene_pos", 1
    )[0]
    assert "viewport.width() / scale" in scene_rect
    assert "viewport.height() / scale" in scene_rect
    assert "pixmap.width() + 2 * margin_x" in scene_rect
    assert "pixmap.height() + 2 * margin_y" in scene_rect
    assert "self._pan_scene_initialized = False" in src


def test_annotation_map_reenable_restores_equal_splitter_panes():
    """Showing the map again must reset the DAPI/map split to 50:50."""
    src = (_PY_DIR / "adjust.py").read_text(encoding="utf-8")
    handler = src.split("def _on_annotation_map_toggled(self, visible: bool):", 1)[1].split(
        "def _toggle_paint_dock", 1
    )[0]
    assert "if visible:" in handler
    assert "QTimer.singleShot(0, self._reset_image_splitter_equal)" in handler
    reset = src.split("def _reset_image_splitter_equal(self):", 1)[1].split(
        "def _toggle_paint_dock", 1
    )[0]
    assert "self.image_splitter.setSizes([left, width - left])" in reset


def test_image_views_use_a_draggable_splitter():
    """The map/DAPI divider is a horizontal QSplitter, not a fixed layout."""
    src = (_PY_DIR / "adjust.py").read_text(encoding="utf-8")
    assert "QSplitter," in src
    assert "self.image_splitter = QSplitter(Qt.Orientation.Horizontal, self)" in src
    assert "self.image_splitter.setChildrenCollapsible(False)" in src
    assert "self.image_splitter.addWidget(self.anno_view)" in src
    assert "self.image_splitter.addWidget(self.img_view)" in src
    assert "self.image_splitter.insertWidget(0, self.anno_view)" in src
    assert "self.image_splitter.splitterMoved.connect(self._on_image_splitter_moved)" in src
    assert "QTimer.singleShot(0, self._refresh_virtual_pan_scene_rects)" in src
