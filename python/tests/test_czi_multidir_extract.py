"""Regression: multi-folder CZI extract resolves files by full path, not basename."""

from __future__ import annotations

import sys
from pathlib import Path

REPO_PY = Path(__file__).resolve().parents[2] / "py"
sys.path.insert(0, str(REPO_PY))

from czi_common import ROLE_DAPI, ROLE_SIGNAL_SOMATA, build_files_lookup, resolve_file_entry  # noqa: E402
from czi_extract import build_work_items  # noqa: E402


def _make_file(dir_path: str, n: int, scan_index: int, slice_id: str) -> dict:
    basename = f"M514({n}).czi"
    return {
        "path": f"{dir_path}/{basename}",
        "basename": basename,
        "source_dir": dir_path,
        "scan_index": scan_index,
        "scene_count": 1,
        "scenes": [{"index": 0, "sliceId": slice_id, "originalSliceId": f"M514({n})"}],
        "channels": [{"index": 0, "label": "DAPI"}, {"index": 1, "label": "Somata"}],
    }


def _multidir_cfg() -> dict:
    """Mirror scripts/test-czi-import.js testBuildSliceOrderTwoDirsDuplicateNames (abbreviated)."""
    day1 = "/day1"
    day2 = "/day2"
    files: list[dict] = []
    slice_order: list[dict] = []
    channels: list[dict] = []
    ordinal = 0
    for scan_index, dir_path in ((0, day1), (1, day2)):
        for n in range(1, 4):
            ordinal += 1
            slice_id = f"M514_s{ordinal:03d}"
            f = _make_file(dir_path, n, scan_index, slice_id)
            files.append(f)
            slice_order.append(
                {
                    "ordinal": ordinal,
                    "sliceId": slice_id,
                    "path": f["path"],
                    "basename": f["basename"],
                    "scene_index": 0,
                    "scan_index": scan_index,
                },
            )
            for ch_idx, role in ((0, ROLE_DAPI), (1, ROLE_SIGNAL_SOMATA)):
                channels.append(
                    {
                        "file": f["path"],
                        "index": ch_idx,
                        "role": role,
                        "keep": True,
                        "other_name": "",
                    },
                )
    return {
        "files": files,
        "slice_order": slice_order,
        "channels": channels,
        "primary_signal_role": ROLE_SIGNAL_SOMATA,
    }


def test_resolve_file_entry_path_over_duplicate_basename() -> None:
    cfg = _multidir_cfg()
    lookup = build_files_lookup(cfg["files"])
    day1_path = "/day1/M514(1).czi"
    day2_path = "/day2/M514(1).czi"
    hit1 = resolve_file_entry(day1_path, lookup)
    hit2 = resolve_file_entry(day2_path, lookup)
    assert hit1 is not None and hit2 is not None
    assert hit1["path"] == day1_path
    assert hit2["path"] == day2_path
    assert resolve_file_entry("M514(1).czi", lookup) is None


def _czi_path(item: dict) -> str:
    """Normalize separators so assertions hold on Windows and POSIX alike."""
    return str(item["czi_path"]).replace("\\", "/")


def test_build_work_items_multidir_distinct_czi_paths() -> None:
    cfg = _multidir_cfg()
    work = build_work_items(cfg)
    assert len(work) == 12  # 6 slices × 2 kept channels
    paths_by_slice: dict[str, set[str]] = {}
    for item in work:
        sid = item["slice_id"]
        paths_by_slice.setdefault(sid, set()).add(_czi_path(item))
    assert len(paths_by_slice) == 6
    for sid, paths in paths_by_slice.items():
        assert len(paths) == 1, f"{sid} mapped to multiple CZI paths: {paths}"
    day1_first = "/day1/M514(1).czi"
    day2_first = "/day2/M514(1).czi"
    assert _czi_path(work[0]) == day1_first
    assert work[0]["slice_id"] == "M514_s001"
    folder2_items = [w for w in work if _czi_path(w).startswith("/day2/")]
    assert len(folder2_items) == 6
    folder2_slices = {w["slice_id"] for w in folder2_items}
    assert folder2_slices == {"M514_s004", "M514_s005", "M514_s006"}
    dup_basename_items = [
        w for w in work if Path(_czi_path(w)).name == "M514(1).czi"
    ]
    assert len(dup_basename_items) == 4  # DAPI + somata for each folder's M514(1).czi
    czi_paths = {_czi_path(w) for w in dup_basename_items}
    assert czi_paths == {day1_first, day2_first}
