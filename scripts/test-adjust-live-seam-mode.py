"""Regression: Adjustment Viewer live seam correction prefers seamgrid geometry."""
from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile

import numpy as np
import tifffile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "py"))
import seam_correct  # noqa: E402


def main() -> int:
    h, w = 96, 240
    img = np.full((h, w), 80, dtype=np.uint8)
    img[:, 80:] += 20
    img[:, 160:] -= 12
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        # Adjustment Viewer passes a display preview such as
        # `<slice>_dapi.png`; that suffix must not become the seamgrid key.
        src = root / "dotted.slice_dapi.tif"
        dest = root / "corrected.png"
        meta = root / ".masonjar"
        (meta / "seamgrid").mkdir(parents=True)
        tifffile.imwrite(src, img)
        (meta / "seamgrid" / "dotted.slice.json").write_text(
            json.dumps(
                {
                    "scene_extent_px": [w, h],
                    "vertical": {"boundaries_frac": [80 / w, 160 / w]},
                    "horizontal": {"boundaries_frac": []},
                }
            ),
            encoding="utf-8",
        )
        info = seam_correct.correct_file_to(
            dest,
            src,
            meta_dir=meta,
            seamgrid_slice_id="dotted.slice",
            geometry_image_path=src,
        )
        assert dest.is_file(), "live preview image was not written"
        assert info["mode"] == "known_geometry", info
        assert info["seam_positions_vertical"] == [80, 160], info
    print("Adjustment live seam: auto known-geometry preference passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
