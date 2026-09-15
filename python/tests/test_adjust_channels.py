"""Viewer/Editor low-res channel discovery (_previews PNG only)."""

from __future__ import annotations

import sys
from pathlib import Path

py_dir = Path(__file__).resolve().parents[2] / "py"
sys.path.insert(0, str(py_dir))

from adjust_channels import (  # noqa: E402
    build_lowres_channel_index,
    lowres_channels_for_slice,
    resolve_previews_dir,
)


def test_resolve_previews_dir_sibling_of_dapi(tmp_path: Path) -> None:
    counting = tmp_path / "data" / "counting"
    dapi = counting / "00_dapi"
    previews = counting / "_previews"
    dapi.mkdir(parents=True)
    previews.mkdir()
    assert resolve_previews_dir(dapi) == previews


def test_resolve_previews_dir_missing_returns_none(tmp_path: Path) -> None:
    dapi = tmp_path / "00_dapi"
    dapi.mkdir()
    assert resolve_previews_dir(dapi) is None


def test_lowres_channels_empty_without_previews(tmp_path: Path) -> None:
    dapi = tmp_path / "00_dapi"
    dapi.mkdir()
    (dapi / "M528_s061.tif").write_bytes(b"dapi")
    channels = lowres_channels_for_slice(dapi, "M528_s061")
    assert channels == []


def test_lowres_channels_dapi_png_fallback(tmp_path: Path) -> None:
    dapi = tmp_path / "00_dapi"
    dapi.mkdir()
    (dapi / "M528_s061.png").write_bytes(b"png")
    channels = lowres_channels_for_slice(dapi, "M528_s061")
    assert len(channels) == 1
    assert channels[0][0] == "DAPI (pipeline)"


def test_lowres_channels_previews_png_only(tmp_path: Path) -> None:
    counting = tmp_path / "counting"
    dapi = counting / "00_dapi"
    previews = counting / "_previews"
    dapi.mkdir(parents=True)
    previews.mkdir()
    (dapi / "M528_s061.tiff").write_bytes(b"dapi")
    (previews / "M528_s061_dapi.png").write_bytes(b"p0")
    (previews / "M528_s061_somata.tif").write_bytes(b"skip tif")
    (previews / "M528_s061_somata.png").write_bytes(b"p1")
    (previews / "M528_s061_rabies_red.png").write_bytes(b"p2")
    (previews / "M528_s061_axons.png").write_bytes(b"p3")
    (previews / "other_slice_somata.png").write_bytes(b"skip")

    channels = lowres_channels_for_slice(dapi, "M528_s061")
    names = [name for name, _ in channels]
    assert names == ["Axons", "Dapi", "Rabies Red", "Somata"]


def test_lowres_channels_dapi_pipeline_fallback(tmp_path: Path) -> None:
    dapi = tmp_path / "00_dapi"
    dapi.mkdir()
    (dapi / "M528_s061.tif").write_bytes(b"dapi")
    (dapi / "M528_s061.png").write_bytes(b"png")
    channels = lowres_channels_for_slice(dapi, "M528_s061")
    assert len(channels) == 1
    assert channels[0][0] == "DAPI (pipeline)"
    assert channels[0][1].name == "M528_s061.png"


def test_lowres_channels_explicit_previews_dir(tmp_path: Path) -> None:
    dapi = tmp_path / "00_dapi"
    custom = tmp_path / "custom_previews"
    dapi.mkdir()
    custom.mkdir()
    (dapi / "M528_s027.tif").write_bytes(b"dapi")
    (custom / "M528_s027_nuclei.png").write_bytes(b"p")

    channels = lowres_channels_for_slice(dapi, "M528_s027", previews_dir=custom)
    assert [n for n, _ in channels] == ["Nuclei"]


def test_session_index_preserves_previews_and_dotted_dapi_fallback(tmp_path: Path) -> None:
    counting = tmp_path / "counting"
    dapi = counting / "00_dapi"
    previews = counting / "_previews"
    dapi.mkdir(parents=True)
    previews.mkdir()
    (previews / "M528_s061_dapi.png").write_bytes(b"preview")
    dotted = dapi / "202607.M554.M579.01.63.png"
    dotted.write_bytes(b"dapi")

    index = build_lowres_channel_index(
        dapi,
        ["M528_s061", "202607", "missing"],
        previews,
    )

    assert [name for name, _ in index["M528_s061"]] == ["Dapi"]
    assert index["202607"] == [("DAPI (pipeline)", dotted)]
    assert index["missing"] == []
    assert lowres_channels_for_slice(
        dapi, "M528_s061", previews, preview_index=index
    ) == index["M528_s061"]
