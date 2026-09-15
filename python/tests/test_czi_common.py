"""Tests for CZI import helpers (py/czi_common.py)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_PY = Path(__file__).resolve().parents[2] / "py"
sys.path.insert(0, str(REPO_PY))

from czi_common import (  # noqa: E402
    ROLE_DAPI,
    ROLE_OTHER,
    ROLE_SIGNAL_SOMATA,
    _safe_print,
    assess_mosaic_import,
    clamp_preview_scale,
    emit_log,
    assess_mosaic_import_metadata,
    bbox_width_height,
    branch_for_channel,
    branch_for_role,
    branch_for_role_key,
    collapse_to_plane_2d,
    collapse_z_stack_to_2d,
    default_slice_id,
    dapi_preview_path,
    orient_dapi_preview_path,
    load_import_config,
    max_output_run_dir,
    m_tile_count_from_czi,
    natural_sort_czi_paths,
    natural_sort_key,
    natural_sort_slice_ids,
    original_scans_path,
    resolve_original_zstack_path,
    parse_section_suffix,
    parse_section_with_identifier,
    preview_plane_to_uint8,
    probe_channels_read,
    read_czi_plane,
    z_indices_with_data,
    role_key_for_channel,
    sanitize_other_name,
    select_largest_plane,
    scene_indices_from_czi,
    signal_preview_path,
    suggest_role_from_label,
    unpack_read_image,
)
from czi_extract import downscale_plane, repair_preview_from_zstack  # noqa: E402


def test_emit_log_cp1252_stdout(monkeypatch) -> None:
    """LOG lines with arrows must not crash on Windows cp1252 consoles."""
    import io

    import czi_common

    buf = io.BytesIO()
    stream = io.TextIOWrapper(buf, encoding="cp1252", errors="strict", line_buffering=True)
    monkeypatch.setattr(sys, "stdout", stream)
    monkeypatch.setattr(czi_common, "_configure_stdio_utf8", lambda: None)
    emit_log("Writing z-stack -> path")
    emit_log("Writing z-stack \u2192 path")
    _safe_print("PROGRESS:50:Loading aicspylibczi \u2192 ready")
    stream.flush()
    out = buf.getvalue().decode("cp1252")
    assert out.count("LOG:Writing z-stack -> path") == 2
    assert "PROGRESS:50:Loading aicspylibczi -> ready" in out


def test_default_slice_id_single_scene() -> None:
    assert default_slice_id("M528_block1.czi", 0, 1) == "M528_block1"


def test_default_slice_id_multi_scene() -> None:
    assert default_slice_id("M528_block1.czi", 2, 3) == "M528_block1_s002"


def test_suggest_role_from_label() -> None:
    assert suggest_role_from_label("DAPI") == ROLE_DAPI
    assert suggest_role_from_label("Rabies soma") == ROLE_SIGNAL_SOMATA


def test_sanitize_other_name() -> None:
    assert sanitize_other_name(" rabies red ") == "rabies_red"
    assert sanitize_other_name("bad/name") == "badname"
    assert sanitize_other_name("") is None
    assert sanitize_other_name("!!!") is None


def test_branch_for_channel_other() -> None:
    ch = {"role": ROLE_OTHER, "other_name": "rabies_red"}
    assert branch_for_channel(ch) == "rabies_red"
    assert role_key_for_channel(ch) == "other:rabies_red"
    assert branch_for_role_key("other:rabies_red") == "rabies_red"


def test_load_import_config_strips_path(tmp_path: Path) -> None:
    cfg_file = tmp_path / "czi_import_config.json"
    cfg_file.write_text('{"czi_import": {"version": 1, "source_dir": "/x"}}', encoding="utf-8")
    loaded = load_import_config(" " + str(cfg_file) + " ")
    assert loaded.get("version") == 1


def test_dapi_preview_path_png(tmp_path: Path) -> None:
    bundle = tmp_path / "Brain_masonjar"
    path = dapi_preview_path(bundle, "M528_s001")
    assert path == bundle / "data/counting/00_dapi/M528_s001.png"
    assert path.suffix.lower() == ".png"


def test_orient_dapi_preview_path_previews(tmp_path: Path) -> None:
    bundle = tmp_path / "Brain_masonjar"
    path = orient_dapi_preview_path(bundle, "M528_s001")
    assert path == bundle / "data/counting/_previews/M528_s001_dapi.png"
    assert "_previews" in path.parts
    assert "00_dapi" not in path.parts


def test_branch_paths(tmp_path: Path) -> None:
    bundle = tmp_path / "Brain_masonjar"
    assert branch_for_role(ROLE_SIGNAL_SOMATA) == "somata"
    out = original_scans_path(
        bundle,
        {"role": ROLE_SIGNAL_SOMATA},
        "M528_s001",
    )
    assert out == bundle / "data/original_scans/somata/M528_s001.tif"
    other_out = original_scans_path(
        bundle,
        {"role": ROLE_OTHER, "other_name": "rabies_red"},
        "M528_s001",
    )
    assert other_out == bundle / "data/original_scans/rabies_red/M528_s001.tif"
    max_dir = max_output_run_dir(bundle, ROLE_SIGNAL_SOMATA, "run-slug")
    assert max_dir == bundle / "data/counting/03_max/somata/max/run-slug"
    other_max = max_output_run_dir(bundle, "other:rabies_red", "run-slug")
    assert other_max == bundle / "data/counting/03_max/rabies_red/max/run-slug"


def test_resolve_original_zstack_path(tmp_path: Path) -> None:
    import numpy as np
    import tifffile as tiff

    bundle = tmp_path / "Brain_masonjar"
    slice_id = "M1"
    flat = bundle / "data/original_scans" / f"{slice_id}.tif"
    nested = bundle / "data/original_scans/dapi" / f"{slice_id}.tif"
    flat.parent.mkdir(parents=True, exist_ok=True)
    tiff.imwrite(flat, np.zeros((2, 2), dtype=np.uint8), photometric="minisblack")
    assert resolve_original_zstack_path(bundle, slice_id, "dapi") == flat

    flat.unlink()
    nested.parent.mkdir(parents=True, exist_ok=True)
    tiff.imwrite(nested, np.zeros((2, 2), dtype=np.uint8), photometric="minisblack")
    assert resolve_original_zstack_path(bundle, slice_id, "dapi") == nested

    assert resolve_original_zstack_path(bundle, slice_id, "somata") is None
    somata = bundle / "data/original_scans/somata" / f"{slice_id}.tif"
    somata.parent.mkdir(parents=True, exist_ok=True)
    tiff.imwrite(somata, np.zeros((2, 2), dtype=np.uint8), photometric="minisblack")
    assert resolve_original_zstack_path(bundle, slice_id, "somata") == somata


def test_parse_section_suffix() -> None:
    assert parse_section_suffix("M528_s112") == 528
    assert parse_section_suffix("M528_s9") == 528
    assert parse_section_suffix("M528_s10") == 528
    assert parse_section_suffix("block210") == 210
    assert parse_section_suffix("M467(57)") == 467


def test_parse_section_with_identifier_m467_paren() -> None:
    assert parse_section_with_identifier("M467(57)", "M467(") == 57
    assert parse_section_with_identifier("M467(108)", "M467(") == 108
    assert parse_section_with_identifier("M467(57)", "M467") is None


def test_natural_sort_key_with_section_identifier() -> None:
    a = natural_sort_key(slice_id="M467(57)", section_identifier="M467(")
    b = natural_sort_key(slice_id="M467(100)", section_identifier="M467(")
    c = natural_sort_key(slice_id="M467(9)", section_identifier="M467(")
    assert c < a < b


def test_natural_sort_paren_suffix() -> None:
    ids = ["M467(100)", "M467(101)", "M467(57)", "M467(58)", "M467(99)", "M467(108)"]
    assert natural_sort_slice_ids(ids) == [
        "M467(57)",
        "M467(58)",
        "M467(99)",
        "M467(100)",
        "M467(101)",
        "M467(108)",
    ]


def test_natural_sort_mixed_widths() -> None:
    ids = ["M528_100", "M528_10", "M528_1", "M528_2"]
    assert natural_sort_slice_ids(ids) == ["M528_1", "M528_2", "M528_10", "M528_100"]


def test_natural_sort_no_trailing_digit() -> None:
    ids = ["M467(57)", "M467(58)", "Brain (1)", "Brain (10)", "Brain (2)"]
    assert natural_sort_slice_ids(ids) == [
        "Brain (1)",
        "Brain (2)",
        "Brain (10)",
        "M467(57)",
        "M467(58)",
    ]


def test_natural_sort_slice_ids() -> None:
    ids = ["M528_s100", "M528_s20", "M528_s9", "M528_s112"]
    assert natural_sort_slice_ids(ids) == ["M528_s9", "M528_s20", "M528_s100", "M528_s112"]


def test_natural_sort_czi_paths(tmp_path: Path) -> None:
    names = ["M528_s10.czi", "M528_s2.czi", "M528_s112.czi"]
    for name in names:
        (tmp_path / name).write_bytes(b"")
    sorted_paths = natural_sort_czi_paths(list(tmp_path.glob("*.czi")))
    assert [p.name for p in sorted_paths] == ["M528_s2.czi", "M528_s10.czi", "M528_s112.czi"]


def test_natural_sort_key_orders_scene_index() -> None:
    a = natural_sort_key(slice_id="block_s001", basename="a.czi", scene_index=1)
    b = natural_sort_key(slice_id="block_s001", basename="a.czi", scene_index=0)
    assert a > b


def test_unpack_read_image_tuple() -> None:
    import numpy as np

    arr = np.zeros((1, 10, 20), dtype=np.uint16)
    data, dims = unpack_read_image((arr, [("Z", 1), ("Y", 10), ("X", 20)]))
    assert data is arr
    assert dims == [("Z", 1), ("Y", 10), ("X", 20)]


def test_unpack_read_image_never_asarray_tuple() -> None:
    import numpy as np

    arr = np.zeros((10, 20), dtype=np.uint16)
    result = (arr, [("Y", 10), ("X", 20)])
    with pytest.raises(ValueError):
        np.asarray(result)
    data, dims = unpack_read_image(result)
    assert data.shape == (10, 20)
    assert dims == [("Y", 10), ("X", 20)]


def test_collapse_to_plane_2d_fixed_dims() -> None:
    import numpy as np

    arr = np.arange(200, dtype=np.uint16).reshape(1, 10, 20)
    plane = collapse_to_plane_2d(arr, [("Z", 1), ("Y", 10), ("X", 20)], fixed={"Z", "S", "C"})
    assert plane.shape == (10, 20)


def test_select_largest_plane_pyramid_stack() -> None:
    import numpy as np

    small = np.zeros((50, 50), dtype=np.uint16)
    large = np.zeros((100, 100), dtype=np.uint16)
    stack = np.array([small, large], dtype=object)
    plane, picked = select_largest_plane(stack)
    assert plane.shape == (100, 100)
    assert picked is True


def test_read_czi_plane_non_mosaic() -> None:
    import numpy as np

    class FakeCzi:
        def is_mosaic(self):
            return False

        def read_image(self, **kwargs):
            arr = np.ones((1, 8, 16), dtype=np.uint16)
            return arr, [("Z", 1), ("Y", 8), ("X", 16)]

    plane = read_czi_plane(FakeCzi(), scene=0, z=0, channel=0)
    assert plane.shape == (8, 16)
    assert plane.dtype == np.uint16


def test_bbox_width_height_object() -> None:
    class BBox:
        w = 1200
        h = 800

    assert bbox_width_height(BBox()) == (1200, 800)
    assert bbox_width_height((0, 0, 640, 480)) == (640, 480)


def test_m_tile_count_from_czi() -> None:
    class FakeCzi:
        def get_dims_shape(self):
            return [{"M": (0, 4), "Z": (0, 1), "C": (0, 2)}]

    assert m_tile_count_from_czi(FakeCzi()) == 4


def test_collapse_z_stack_to_2d_single_plane() -> None:
    import numpy as np

    stack = np.arange(12, dtype=np.uint16).reshape(1, 3, 4)
    plane = collapse_z_stack_to_2d(stack)
    assert plane.shape == (3, 4)
    assert np.array_equal(plane, stack[0])

    already_2d = np.zeros((5, 6), dtype=np.uint8)
    assert collapse_z_stack_to_2d(already_2d) is already_2d


def test_assess_mosaic_import_m_tiles_informational_only() -> None:
    class SceneBBox:
        x = 0
        y = 0
        w = 2000
        h = 1500

    class TileBBox:
        pass

    class FakeCzi:
        def is_mosaic(self):
            return True

        def get_dims_shape(self):
            return [{"M": (0, 3), "Z": (0, 1), "C": (0, 1), "S": (0, 1)}]

        def get_mosaic_scene_bounding_box(self, index=0):
            return SceneBBox()

        def get_all_mosaic_tile_bounding_boxes(self, **kwargs):
            tiles = {}
            for i in range(3):
                t = TileBBox()
                t.x = (i % 2) * 1000
                t.y = 0
                t.w = 1000
                t.h = 1500
                tiles[i] = t
            return tiles

        def read_mosaic(self, **kwargs):
            raise AssertionError("probe path must not read_mosaic")

    info = assess_mosaic_import(FakeCzi(), sample_read=False)
    assert info["likely_unstitched"] is False
    assert info["m_tile_count"] == 3
    assert info["mosaic_stitch_status"] == "ok"
    assert any("tile index" in w.lower() for w in info["mosaic_warnings"])
    assert not any("stitch in ZEN" in w for w in info["mosaic_warnings"])


def test_assess_mosaic_import_metadata_full_coverage() -> None:
    class SceneBBox:
        x = 0
        y = 0
        w = 1000
        h = 800

    class TileBBox:
        pass

    class FakeCzi:
        def is_mosaic(self):
            return True

        def get_dims_shape(self):
            return [{"M": (0, 2), "Z": (0, 1), "C": (0, 1), "S": (0, 1)}]

        def get_mosaic_scene_bounding_box(self, index=0):
            return SceneBBox()

        def get_all_mosaic_tile_bounding_boxes(self, **kwargs):
            t0, t1 = TileBBox(), TileBBox()
            t0.x, t0.y, t0.w, t0.h = 0, 0, 500, 800
            t1.x, t1.y, t1.w, t1.h = 500, 0, 500, 800
            return {0: t0, 1: t1}

        def read_mosaic(self, **kwargs):
            raise AssertionError("metadata assess must not read_mosaic")

    info = assess_mosaic_import_metadata(FakeCzi())
    assert info["mosaic_stitch_status"] == "ok"
    assert info["likely_unstitched"] is False


def test_assess_mosaic_import_metadata_single_tile_suspect() -> None:
    class SceneBBox:
        x = 0
        y = 0
        w = 4000
        h = 3000

    class TileBBox:
        x = 0
        y = 0
        w = 600
        h = 500

    class FakeCzi:
        def is_mosaic(self):
            return True

        def get_dims_shape(self):
            return [{"M": (0, 2), "Z": (0, 1), "C": (0, 1), "S": (0, 1)}]

        def get_mosaic_scene_bounding_box(self, index=0):
            return SceneBBox()

        def get_all_mosaic_tile_bounding_boxes(self, **kwargs):
            return {0: TileBBox()}

        def read_mosaic(self, **kwargs):
            raise AssertionError("metadata assess must not read_mosaic")

    info = assess_mosaic_import_metadata(FakeCzi())
    assert info["mosaic_stitch_status"] == "suspect"
    assert info["likely_unstitched"] is True
    assert any("stitch" in w.lower() for w in info["mosaic_warnings"])


def test_probe_file_uses_metadata_only_assess(monkeypatch) -> None:
    import types

    import czi_probe

    read_calls: list[dict] = []

    class FakeCzi:
        def get_dims_shape(self):
            return [{"Z": (0, 1), "C": (0, 1), "S": (0, 1)}]

        def is_mosaic(self):
            return True

        def read_mosaic(self, **kwargs):
            read_calls.append(kwargs)
            raise AssertionError("probe_file must not call read_mosaic")

    def fake_assess(czi, **kwargs):
        assert kwargs.get("sample_read") is False
        return {
            "is_mosaic": True,
            "has_m_dim": False,
            "m_tile_count": 1,
            "likely_unstitched": False,
            "mosaic_stitch_status": "ok",
            "mosaic_warnings": [],
        }

    class FakeCziFile:
        def __init__(self, path):
            self._czi = FakeCzi()

        def __getattr__(self, name):
            return getattr(self._czi, name)

    fake_mod = types.ModuleType("aicspylibczi")
    fake_mod.CziFile = FakeCziFile
    def fake_channel_probe(czi, scene=0):
        return [], []

    monkeypatch.setattr(czi_probe, "assess_mosaic_import", fake_assess)
    monkeypatch.setattr(czi_probe, "probe_channels_read", fake_channel_probe)
    monkeypatch.setitem(sys.modules, "aicspylibczi", fake_mod)
    result = czi_probe.probe_file(Path("sample.czi"))
    assert result["is_mosaic"] is True
    assert result["mosaic_stitch_status"] == "ok"
    assert read_calls == []


def test_assess_mosaic_import_bbox_mismatch() -> None:
    import numpy as np

    class BBox:
        w = 2000
        h = 1500

    class FakeCzi:
        def is_mosaic(self):
            return True

        def get_dims_shape(self):
            return [{"M": (0, 2), "Z": (0, 1), "C": (0, 1), "S": (0, 1)}]

        def read_mosaic(self, **kwargs):
            plane = np.zeros((500, 600), dtype=np.uint16)
            return plane, [("Y", 500), ("X", 600)]

        def get_mosaic_scene_bounding_box(self, index=0):
            return BBox()

        def get_all_mosaic_tile_bounding_boxes(self, **kwargs):
            class TileBBox:
                pass

            t0, t1 = TileBBox(), TileBBox()
            t0.w, t0.h = 600, 500
            t1.w, t1.h = 600, 500
            return {0: t0, 1: t1}

    info = assess_mosaic_import(FakeCzi(), sample_read=True)
    assert info["likely_unstitched"] is True
    assert info["mosaic_stitch_status"] == "suspect"
    assert any("bounding box" in w.lower() or "tile" in w.lower() for w in info["mosaic_warnings"])


def test_assess_mosaic_import_non_mosaic_clean() -> None:
    class FakeCzi:
        def is_mosaic(self):
            return False

        def get_dims_shape(self):
            return [{"Z": (0, 5), "C": (0, 1)}]

    info = assess_mosaic_import(FakeCzi(), sample_read=False)
    assert info["likely_unstitched"] is False
    assert info["mosaic_warnings"] == []


def test_assess_mosaic_import_zen_stitched_full_coverage() -> None:
    """ZEN-stitched mosaics keep M>1 but read_mosaic returns full scene bbox."""
    import numpy as np

    class BBox:
        x = 0
        y = 0
        w = 2000
        h = 1500

    class FakeCzi:
        def is_mosaic(self):
            return True

        def get_dims_shape(self):
            return [{"M": (0, 30), "Z": (0, 1), "C": (0, 1), "S": (0, 1)}]

        def read_mosaic(self, **kwargs):
            assert "S" not in kwargs
            plane = np.zeros((1500, 2000), dtype=np.uint16)
            return plane, [("Y", 1500), ("X", 2000)]

        def get_mosaic_scene_bounding_box(self, index=0):
            return BBox()

    info = assess_mosaic_import(FakeCzi(), sample_read=True)
    assert info["likely_unstitched"] is False
    assert info["mosaic_stitch_status"] == "ok"
    assert info["m_tile_count"] == 30
    assert not any("stitch in ZEN" in w for w in info["mosaic_warnings"])


def test_read_czi_plane_mosaic_uses_read_mosaic() -> None:
    import numpy as np

    class FakeCzi:
        def __init__(self):
            self.calls = []

        def is_mosaic(self):
            return True

        def get_dims_shape(self):
            return [{"M": (0, 4), "Z": (0, 2), "C": (0, 4), "S": (0, 3)}]

        def get_mosaic_scene_bounding_box(self, index=0):
            class BBox:
                x = 100
                y = 200
                w = 800
                h = 600

            return BBox()

        def read_mosaic(self, **kwargs):
            self.calls.append(kwargs)
            if "S" in kwargs:
                raise ValueError("Do not set S when reading mosaic files!")
            arr = np.ones((1, 12, 24), dtype=np.uint8)
            return arr, [("Y", 12), ("X", 24)]

        def read_image(self, **kwargs):
            raise AssertionError("read_image should not be called for mosaic files")

    czi = FakeCzi()
    plane = read_czi_plane(czi, scene=2, z=1, channel=3)
    assert plane.shape == (12, 24)
    assert czi.calls == [
        {
            "scale_factor": 1.0,
            "Z": 1,
            "C": 3,
            "region": (100, 200, 800, 600),
        }
    ]


def test_read_czi_plane_mosaic_sample_scale() -> None:
    import numpy as np

    class FakeCzi:
        def __init__(self):
            self.calls = []

        def is_mosaic(self):
            return True

        def get_dims_shape(self):
            return [{"M": (0, 1), "Z": (0, 1), "C": (0, 1), "S": (0, 1)}]

        def read_mosaic(self, **kwargs):
            self.calls.append(kwargs)
            arr = np.ones((6, 8), dtype=np.uint8)
            return arr, [("Y", 6), ("X", 8)]

    czi = FakeCzi()
    plane = read_czi_plane(czi, scene=0, z=0, channel=0, sample_scale=0.05)
    assert plane.shape == (6, 8)
    assert czi.calls == [{"scale_factor": 0.05, "Z": 0, "C": 0}]


def test_downscale_plane_linear_005() -> None:
    import cv2

    import czi_extract

    czi_extract.cv2 = cv2
    plane = __import__("numpy").zeros((2000, 1000), dtype=__import__("numpy").uint16)
    out = downscale_plane(plane, 0.05)
    assert out.shape == (100, 50)


def test_preview_plane_to_uint8_uint16() -> None:
    import numpy as np

    plane = np.array([[0, 32768], [65535, 1000]], dtype=np.uint16)
    out = preview_plane_to_uint8(plane)
    assert out.dtype == np.uint8
    assert out.max() <= 255

    low = np.full((32, 32), 400, dtype=np.uint16)
    low[8:24, 8:24] = 500
    out_low = preview_plane_to_uint8(low)
    assert out_low.max() > 200


def test_clamp_preview_scale() -> None:
    assert clamp_preview_scale(0.05) == 0.05
    assert clamp_preview_scale(0.1) == 0.05


def test_repair_preview_from_zstack(tmp_path: Path) -> None:
    import numpy as np
    import tifffile

    import czi_extract

    czi_extract.np = np
    czi_extract.tiff = tifffile
    import cv2

    czi_extract.cv2 = cv2

    bundle = tmp_path / "Brain_masonjar"
    bundle.mkdir()
    ch = {"role": ROLE_SIGNAL_SOMATA, "index": 1, "keep": True}
    slice_id = "M528_s001"
    z_path = original_scans_path(bundle, ch, slice_id)
    z_path.parent.mkdir(parents=True)
    stack = (np.arange(200 * 200 * 3, dtype=np.uint16).reshape(3, 200, 200))
    tifffile.imwrite(str(z_path), stack, photometric="minisblack")
    assert repair_preview_from_zstack(bundle, ch, slice_id, 0.05) is True
    prev = signal_preview_path(bundle, slice_id, ch)
    assert prev.is_file()
    assert prev.suffix.lower() == ".png"
    arr = cv2.imread(str(prev), cv2.IMREAD_UNCHANGED)
    assert arr.dtype == np.uint8
    assert arr.shape == (10, 10)
    assert arr.max() > 200


def test_read_czi_plane_mosaic_single_scene_no_region() -> None:
    import numpy as np

    class FakeCzi:
        def __init__(self):
            self.calls = []

        def is_mosaic(self):
            return True

        def get_dims_shape(self):
            return [{"M": (0, 30), "Z": (0, 1), "C": (0, 1), "S": (0, 1)}]

        def read_mosaic(self, **kwargs):
            self.calls.append(kwargs)
            assert "S" not in kwargs
            assert "region" not in kwargs
            arr = np.ones((12, 24), dtype=np.uint8)
            return arr, [("Y", 12), ("X", 24)]

    czi = FakeCzi()
    plane = read_czi_plane(czi, scene=0, z=0, channel=0)
    assert plane.shape == (12, 24)
    assert czi.calls == [{"scale_factor": 1.0, "Z": 0, "C": 0}]


def _fake_czi(dims_shape, *, is_mosaic=False, shape_is_consistent=True):
    class FakeCzi:
        def __init__(self):
            self.shape_is_consistent = shape_is_consistent

        def get_dims_shape(self):
            return dims_shape

        def is_mosaic(self):
            return is_mosaic

    return FakeCzi()


def test_scene_indices_single_scene_multi_z() -> None:
    czi = _fake_czi([{"S": (0, 1), "Z": (0, 60), "C": (0, 4)}])
    assert scene_indices_from_czi(czi) == [0]


def test_scene_indices_multi_scene_consistent() -> None:
    czi = _fake_czi([{"S": (0, 3), "Z": (0, 10), "C": (0, 2)}])
    assert scene_indices_from_czi(czi) == [0, 1, 2]


def test_scene_indices_multi_scene_inconsistent() -> None:
    dims = [{"S": (0, 1), "Z": (0, 5)}, {"S": (1, 2), "Z": (0, 5)}, {"S": (2, 3), "Z": (0, 5)}]
    czi = _fake_czi(dims, shape_is_consistent=False)
    assert scene_indices_from_czi(czi) == [0, 1, 2]


def test_scene_indices_many_z_blocks_no_s() -> None:
    dims = [{"Z": (i, i + 1)} for i in range(57)]
    czi = _fake_czi(dims, shape_is_consistent=False)
    assert scene_indices_from_czi(czi) == [0]
    assert scene_indices_from_czi(czi) != list(range(57))


def test_scene_indices_mosaic_multi_block() -> None:
    dims = [{"Z": (i, i + 1), "M": (0, 4)} for i in range(10)]
    czi = _fake_czi(dims, is_mosaic=True, shape_is_consistent=False)
    assert scene_indices_from_czi(czi) == [0]


def test_z_indices_with_data_sparse() -> None:
    import numpy as np

    class FakeCzi:
        def is_mosaic(self):
            return True

        def get_dims_shape(self):
            return [{"Z": (0, 9), "C": (0, 3), "M": (0, 2), "S": (0, 1)}]

        def get_all_mosaic_tile_bounding_boxes(self, **kwargs):
            z = kwargs.get("Z", 0)
            if z == 4:
                return {0: type("B", (), {"x": 0, "y": 0, "w": 10, "h": 10})()}
            return {}

    assert z_indices_with_data(FakeCzi(), 0, 2, log_sparse=False) == [4]


class PylibCZI_PixelTypeException(Exception):
    pass


def test_read_czi_plane_mosaic_pixel_type_fallback() -> None:
    import numpy as np

    class FakeCzi:
        def is_mosaic(self):
            return True

        def get_dims_shape(self):
            return [{"Z": (0, 1), "C": (0, 1), "M": (0, 1), "S": (0, 1)}]

        def read_mosaic(self, **kwargs):
            raise PylibCZI_PixelTypeException(
                "PixelType( Unknown type ): Pixel Type unsupported by libCZI."
            )

        def read_image(self, **kwargs):
            arr = np.full((8, 12), 42, dtype=np.uint8)
            return arr, [("Y", 8), ("X", 12)]

    plane = read_czi_plane(FakeCzi(), scene=0, z=0, channel=2)
    assert plane.shape == (8, 12)
    assert int(plane[0, 0]) == 42


def test_read_czi_plane_mosaic_tile_fallback() -> None:
    import numpy as np

    class BBox:
        def __init__(self, x, y, w, h):
            self.x, self.y, self.w, self.h = x, y, w, h

    class FakeCzi:
        def is_mosaic(self):
            return True

        def get_dims_shape(self):
            return [{"Z": (0, 1), "C": (0, 1), "M": (0, 2), "S": (0, 1)}]

        def get_mosaic_scene_bounding_box(self, index=0):
            return BBox(0, 0, 20, 10)

        def read_mosaic(self, **kwargs):
            raise PylibCZI_PixelTypeException("unsupported")

        def read_image(self, **kwargs):
            if "M" in kwargs:
                arr = np.full((10, 10), int(kwargs["M"]) + 1, dtype=np.uint8)
                return arr, [("Y", 10), ("X", 10)]
            raise PylibCZI_PixelTypeException("unsupported")

        def get_all_mosaic_tile_bounding_boxes(self, **kwargs):
            return {
                0: BBox(0, 0, 10, 10),
                1: BBox(10, 0, 10, 10),
            }

    plane = read_czi_plane(FakeCzi(), scene=0, z=0, channel=0)
    assert plane.shape == (10, 20)
    assert int(plane[0, 0]) == 1
    assert int(plane[0, 10]) == 2


def test_probe_channel_pixel_probe_sparse() -> None:
    import numpy as np

    class FakeCzi:
        pixel_type = "gray16"

        def is_mosaic(self):
            return False

        def get_dims_shape(self):
            return [{"Z": (0, 9), "C": (0, 3), "S": (0, 1)}]

        def get_tile_bounding_box(self, **kwargs):
            if kwargs.get("Z") == 4 and kwargs.get("C") == 2:
                return type("B", (), {"x": 0, "y": 0, "w": 4, "h": 4})()
            return type("B", (), {"x": 0, "y": 0, "w": 0, "h": 0})()

        def read_image(self, **kwargs):
            if kwargs.get("C") == 2:
                raise PylibCZI_PixelTypeException("bad ch2")
            arr = np.ones((4, 4), dtype=np.uint8)
            return arr, [("Y", 4), ("X", 4)]

        def read_mosaic(self, **kwargs):
            raise AssertionError("not mosaic")

    probe, warnings = probe_channels_read(FakeCzi(), scene=0)
    ch2 = next(p for p in probe if p["index"] == 2)
    assert ch2["sparse_z"] is True
    assert ch2["z_with_data"] == [4]
    assert ch2["ok"] is False
    assert any("sample read failed" in w for w in warnings)


def test_probe_channels_read_mosaic_skips_per_z_tile_bbox() -> None:
    """Mosaic probe must not enumerate tile bboxes for every Z×C."""
    import numpy as np

    bbox_calls: list[dict] = []

    class FakeCzi:
        pixel_type = "gray16"

        def is_mosaic(self):
            return True

        def get_dims_shape(self):
            return [{"Z": (0, 40), "C": (0, 3), "M": (0, 4), "S": (0, 1)}]

        def get_all_mosaic_tile_bounding_boxes(self, **kwargs):
            bbox_calls.append(dict(kwargs))
            return {0: type("B", (), {"x": 0, "y": 0, "w": 10, "h": 10})()}

        def read_mosaic(self, **kwargs):
            arr = np.ones((4, 4), dtype=np.uint8)
            return arr, [("Y", 4), ("X", 4)]

        def read_image(self, **kwargs):
            raise AssertionError("read_image should not be needed")

    probe, warnings = probe_channels_read(FakeCzi(), scene=0)
    assert bbox_calls == []
    assert len(probe) == 3
    for entry in probe:
        assert entry["sparse_z"] is False
        assert entry["z_count"] == 40
        assert entry["z_with_data"] == list(range(40))
        assert entry["ok"] is True
    assert warnings == []


def test_probe_channels_read_skips_tile_composite_fallback() -> None:
    """Probe sample reads must soft-fail without full-res tile composite."""
    import numpy as np

    tile_bbox_calls = 0
    tile_read_calls = 0

    class BBox:
        def __init__(self, x, y, w, h):
            self.x, self.y, self.w, self.h = x, y, w, h

    class FakeCzi:
        pixel_type = "gray16"

        def is_mosaic(self):
            return True

        def get_dims_shape(self):
            return [{"Z": (0, 5), "C": (0, 2), "M": (0, 2), "S": (0, 1)}]

        def get_mosaic_scene_bounding_box(self, index=0):
            return BBox(0, 0, 20, 10)

        def read_mosaic(self, **kwargs):
            raise PylibCZI_PixelTypeException(
                "PixelType( Unknown type ): Pixel Type unsupported by libCZI."
            )

        def read_image(self, **kwargs):
            nonlocal tile_read_calls
            if "M" in kwargs:
                tile_read_calls += 1
                arr = np.full((10, 10), 1, dtype=np.uint8)
                return arr, [("Y", 10), ("X", 10)]
            raise PylibCZI_PixelTypeException(
                "PixelType( Unknown type ): Pixel Type unsupported by libCZI."
            )

        def get_all_mosaic_tile_bounding_boxes(self, **kwargs):
            nonlocal tile_bbox_calls
            tile_bbox_calls += 1
            return {
                0: BBox(0, 0, 10, 10),
                1: BBox(10, 0, 10, 10),
            }

    probe, warnings = probe_channels_read(FakeCzi(), scene=0)
    assert tile_bbox_calls == 0
    assert tile_read_calls == 0
    assert all(entry["ok"] is False for entry in probe)
    assert any("sample read failed" in w for w in warnings)
    assert any("skipping further sample" in w for w in warnings)
    # First channel fails; remaining channels skip without more I/O
    assert probe[0]["error"]
    assert "sample read skipped" in probe[1]["error"]


def test_read_czi_plane_no_tile_composite_when_disallowed() -> None:
    class FakeCzi:
        def is_mosaic(self):
            return True

        def get_dims_shape(self):
            return [{"Z": (0, 1), "C": (0, 1), "M": (0, 2), "S": (0, 1)}]

        def read_mosaic(self, **kwargs):
            raise RuntimeError("mosaic boom")

        def read_image(self, **kwargs):
            raise RuntimeError("read_image boom")

        def get_all_mosaic_tile_bounding_boxes(self, **kwargs):
            raise AssertionError("tile composite must not run")

    try:
        read_czi_plane(
            FakeCzi(),
            scene=0,
            z=0,
            channel=0,
            allow_tile_composite=False,
        )
        raise AssertionError("expected RuntimeError")
    except RuntimeError as exc:
        msg = str(exc)
        assert "mosaic boom" in msg
        assert "read_image boom" in msg
        assert "tiles=" not in msg


def test_extract_sparse_z_single_plane(tmp_path: Path, monkeypatch) -> None:
    """One sparse Z with data writes a 2D TIFF (no full Z loop)."""
    import numpy as np

    import czi_extract
    import tifffile

    monkeypatch.setattr(czi_extract, "np", np)
    monkeypatch.setattr(czi_extract, "tiff", tifffile)

    read_calls: list[int] = []

    class FakeCzi:
        def is_mosaic(self):
            return True

        def get_dims_shape(self):
            return [{"Z": (0, 9), "C": (0, 1), "M": (0, 1), "S": (0, 1)}]

        def get_all_mosaic_tile_bounding_boxes(self, **kwargs):
            if kwargs.get("Z") == 4:
                return {0: type("B", (), {"x": 0, "y": 0, "w": 4, "h": 4})()}
            return {}

        def read_mosaic(self, **kwargs):
            read_calls.append(int(kwargs.get("Z", -1)))
            arr = np.full((4, 4), 7, dtype=np.uint8)
            return arr, [("Y", 4), ("X", 4)]

    out_path = tmp_path / "slice.tif"
    czi_extract.extract_z_stack(
        FakeCzi(),
        scene=0,
        channel=0,
        z_indices=[4],
        out_path=out_path,
        preview_path=None,
        preview_scale=0.05,
        slice_id="M528_s001",
    )
    assert read_calls == [4]
    assert out_path.is_file()

    data = tifffile.imread(str(out_path))
    assert data.ndim == 2
    assert data.shape == (4, 4)


def test_extract_multi_z_preview_no_truthiness_error(
    tmp_path: Path, monkeypatch
) -> None:
    """Multi-Z signal extract must not use ndarray truthiness for preview pick."""
    import numpy as np

    import cv2
    import czi_extract
    import tifffile

    monkeypatch.setattr(czi_extract, "np", np)
    monkeypatch.setattr(czi_extract, "tiff", tifffile)
    monkeypatch.setattr(czi_extract, "cv2", cv2)

    def fake_read_plane(_czi, _scene, z, _channel):
        # Z=1 plane is brighter so preview should not fall back to planes[0] only.
        val = 200 if z == 1 else 10
        return np.full((8, 8), val, dtype=np.uint8)

    monkeypatch.setattr(czi_extract, "read_plane", fake_read_plane)

    bundle_root = tmp_path / "bundle"
    bundle_root.mkdir()
    out_path = bundle_root / "data" / "original_scans" / "somata" / "M553_s001.tif"
    preview_path = bundle_root / "data" / "counting" / "_previews" / "M553_s001_somata.png"

    czi_extract.extract_z_stack(
        object(),
        scene=0,
        channel=0,
        z_indices=[0, 1],
        out_path=out_path,
        preview_path=preview_path,
        preview_scale=0.05,
        slice_id="M553_s001",
        bundle_root=bundle_root,
        role_key="signal_somata",
    )

    assert out_path.is_file()
    stack = tifffile.imread(str(out_path))
    assert stack.ndim == 3
    assert stack.shape[0] == 2
    assert preview_path.is_file()
