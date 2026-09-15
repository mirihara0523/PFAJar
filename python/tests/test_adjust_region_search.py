"""Adjust paint-region search: tier-scoped, case-insensitive completer source."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_PY_DIR = _REPO_ROOT / "py"
if str(_PY_DIR) not in sys.path:
    sys.path.insert(0, str(_PY_DIR))

pytest.importorskip("qtpy.QtWidgets")

from structure_catalog import load_catalog  # noqa: E402

_GRAPH_PATH = _REPO_ROOT / "csv" / "structure_graph.json"


class _FakeCombo:
    def __init__(self, items):
        self._items = list(items)
        self._index = 0

    def count(self):
        return len(self._items)

    def currentData(self):
        if not self._items:
            return None
        return self._items[self._index][1]

    def set_index_for_data(self, data):
        for i, (_label, value) in enumerate(self._items):
            if value == data:
                self._index = i
                return


class _ViewerStub:
    def __init__(self, catalog, *, tier_id="areas", ccf_advanced=False):
        self.catalog = catalog
        self.ccf_advanced = ccf_advanced
        self.tier_combo = _FakeCombo(
            [
                ("Functional areas", "areas"),
                ("Cortical layers", "layers"),
                ("Major divisions", "major"),
            ]
        )
        self.tier_combo.set_index_for_data(tier_id)
        self.level_combo = _FakeCombo([("Level 6", 6), ("Level 11", 11)])

    def _current_tier_id(self):
        if self.tier_combo.count() == 0:
            return None
        data = self.tier_combo.currentData()
        return str(data) if data is not None else None

    def _current_catalog_level(self):
        if self.level_combo.count() == 0:
            return None
        level = self.level_combo.currentData()
        return int(level) if level is not None else None


def _viewer_stub(catalog, *, tier_id="areas", ccf_advanced=False):
    return _ViewerStub(catalog, tier_id=tier_id, ccf_advanced=ccf_advanced)


def test_current_regions_tier_scoped_vis_query() -> None:
    import adjust  # noqa: E402

    catalog = load_catalog(_GRAPH_PATH)
    areas_self = _viewer_stub(catalog, tier_id="areas")
    layers_self = _viewer_stub(catalog, tier_id="layers")

    areas_vis = adjust.AnnotationViewer._current_regions(areas_self, "VIS")
    layers_vis = adjust.AnnotationViewer._current_regions(layers_self, "VIS")
    assert 0 < len(areas_vis) < len(catalog["by_id"])
    assert len(layers_vis) > 0
    assert len(areas_vis) != len(layers_vis)

    full_catalog = adjust.AnnotationViewer._flatten_catalog_regions(areas_self, "VIS")
    assert len(full_catalog) >= len(areas_vis)


def test_current_regions_case_insensitive_query() -> None:
    import adjust  # noqa: E402

    catalog = load_catalog(_GRAPH_PATH)
    stub = _viewer_stub(catalog, tier_id="areas")
    lower = adjust.AnnotationViewer._current_regions(stub, "vis")
    upper = adjust.AnnotationViewer._current_regions(stub, "VIS")
    assert [n["id"] for n in lower] == [n["id"] for n in upper]
    assert len(lower) > 0


def test_current_regions_accepts_full_region_name() -> None:
    import adjust  # noqa: E402

    catalog = load_catalog(_GRAPH_PATH)
    stub = _viewer_stub(catalog, tier_id="areas")
    candidate = next(node for node in adjust.AnnotationViewer._current_regions(stub)
                     if node.get("name"))
    matches = adjust.AnnotationViewer._current_regions(stub, candidate["name"])
    assert any(node["id"] == candidate["id"] for node in matches)


def test_search_completer_uses_current_regions_not_full_catalog() -> None:
    import adjust  # noqa: E402

    catalog = load_catalog(_GRAPH_PATH)
    stub = _viewer_stub(catalog, tier_id="areas")
    scoped = adjust.AnnotationViewer._current_regions(stub, "")
    full = adjust.AnnotationViewer._flatten_catalog_regions(stub, "")
    assert 0 < len(scoped) < len(full)
