"""Generate a synthetic seam-correction fixture."""
from pathlib import Path
import argparse
import numpy as np
import tifffile
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "py"))
import seam_correct


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--output", default=".test-seam-fixture.tif")
    args = ap.parse_args()
    h, w = 256, 620
    y, x = np.mgrid[:h, :w]
    img = (80 + 8 * np.sin(y / 17.0) + 5 * np.cos(x / 23.0)).astype(np.float32)
    # Five periodic tile boundaries with alternating brightness offsets.
    for boundary, offset in zip((100, 200, 300, 400, 500), (18, -12, 15, -10, 14)):
        img[:, boundary:] += offset
    # Local structures that should not be classified as seams.
    img[70:190, 535:545] += 35
    img[90:170, 565:575] -= 25
    img = np.clip(img, 0, 255).astype(np.uint8)
    out = Path(args.output).resolve()
    tifffile.imwrite(out, img)
    print(f"fixture={out} shape={img.shape} boundaries=[100,200,300,400,500]")
    tissue = img > 8
    grid = {"vertical": {"boundaries_frac": [100/620, 200/620, 300/620, 400/620, 500/620]},
            "horizontal": {"boundaries_frac": []}}
    known, known_info = seam_correct.correct_known_geometry(img, grid, band=4)
    estimated, estimated_info = seam_correct.correct(img, band=4)
    print("known_geometry", known_info.get("seam_positions_vertical"), known_info.get("seam_residuals"))
    print("grid_estimated", estimated_info.get("seam_positions"), estimated_info.get("n_seams"))
    assert known_info['seam_positions_vertical'] == [100, 200, 300, 400, 500]
    if not all(item.get("improved") for item in known_info.get("seam_residuals", [])):
        raise AssertionError("Known-geometry residual did not improve for every synthetic seam")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
