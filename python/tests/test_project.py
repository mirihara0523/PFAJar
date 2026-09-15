"""Tests for Mason Jar / Bell Jar project bundle schema and I/O."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from belljar.io import project as project_io
from belljar.io import file_index as file_index_io
from belljar.types import (
    CANONICAL_ROLE_PATHS,
    LAYOUT_BELLJAR_V1,
    LAYOUT_MASONJAR_V1,
    PROJECT_VERSION_V1,
    BelljarProject,
)


def test_new_v1_bundle_defaults(tmp_path: Path) -> None:
    proj = BelljarProject.new_v1_bundle("TestBrain")
    assert proj.version == PROJECT_VERSION_V1
    assert proj.layout == LAYOUT_MASONJAR_V1
    assert proj.roles == CANONICAL_ROLE_PATHS
    assert proj.created
    assert proj.modified


def test_save_load_roundtrip(tmp_path: Path) -> None:
    bundle = tmp_path / "M001.masonjar"
    bundle.mkdir()
    project_io.ensure_bundle_layout(bundle)
    proj_path = bundle / project_io.PROJECT_FILENAMES[0]
    project = BelljarProject.new_v1_bundle("M001")
    project.settings = {"align": {"hemisphere": "W"}}
    project_io.save_project(project, proj_path)

    loaded = project_io.load_project(bundle)
    assert loaded.name == "M001"
    assert loaded.layout == LAYOUT_MASONJAR_V1
    assert loaded.settings["align"]["hemisphere"] == "W"


def test_legacy_belljar_bundle(tmp_path: Path) -> None:
    bundle = tmp_path / "Legacy.belljar"
    bundle.mkdir()
    project_io.ensure_bundle_layout(bundle)
    (bundle / ".belljar").mkdir(exist_ok=True)
    proj = BelljarProject.new_v1_bundle("Legacy")
    proj.layout = LAYOUT_BELLJAR_V1
    legacy_path = bundle / "project.belljar"
    project_io.save_project(proj, legacy_path)

    loaded = project_io.load_project(bundle)
    assert loaded.name == "Legacy"
    assert loaded.layout == LAYOUT_BELLJAR_V1
    assert project_io.project_file_path(bundle) == legacy_path


def test_validate_missing_role_dir(tmp_path: Path) -> None:
    bundle = tmp_path / "Brain.masonjar"
    bundle.mkdir()
    project_io.ensure_bundle_layout(bundle)
    proj = BelljarProject.new_v1_bundle("Brain")
    project_io.save_project(proj, bundle / project_io.PROJECT_FILENAMES[0])

    errors = project_io.validate_project(bundle)
    # Empty role dirs exist after ensure_bundle_layout — should validate
    assert errors == []


def test_validate_detects_missing_project_file(tmp_path: Path) -> None:
    bundle = tmp_path / "Empty.masonjar"
    bundle.mkdir()
    errors = project_io.validate_project(bundle)
    assert any("project.masonjar" in e or "project.belljar" in e for e in errors)


def test_build_manifest_slice_index(tmp_path: Path) -> None:
    bundle = tmp_path / "Brain.masonjar"
    bundle.mkdir()
    project_io.ensure_bundle_layout(bundle)
    dapi_dir = bundle / CANONICAL_ROLE_PATHS["dapi"]
    (dapi_dir / "M528_s061.ome.tiff").write_bytes(b"x")

    manifest_path = project_io.build_manifest(bundle)
    data = json.loads(manifest_path.read_text())
    assert data["version"] == 1
    assert len(data["slices"]) == 1
    assert data["slices"][0]["sliceId"] == "M528_s061"


def test_compute_match_report_quality(tmp_path: Path) -> None:
    index = {
        "files": [
            {
                "sliceId": "M528_s061",
                "role": "dapi",
                "metadata": {"width": 512, "height": 512},
            },
            {
                "sliceId": "M528_s061",
                "role": "max",
                "metadata": {"width": 2048, "height": 2048},
            },
        ]
    }
    report = file_index_io.compute_match_report(index)
    assert report["matchedSliceIds"] == ["M528_s061"]
    assert any(q["code"] == "resolution_mismatch" for q in report["qualityIssues"])


def test_import_role_copy(tmp_path: Path) -> None:
    bundle = tmp_path / "Brain.masonjar"
    source = tmp_path / "source_dapi"
    source.mkdir()
    (source / "a.tif").write_bytes(b"1")

    project_io.ensure_bundle_layout(bundle)
    roles = dict(CANONICAL_ROLE_PATHS)
    entry = project_io.import_role(bundle, "dapi", source, mode="copy", roles=roles)
    assert "error" not in entry
    dest = bundle / CANONICAL_ROLE_PATHS["dapi"]
    assert (dest / "a.tif").is_file()
