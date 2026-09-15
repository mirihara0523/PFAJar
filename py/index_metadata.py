"""Extract width/height metadata from image files (stdout JSON)."""

from __future__ import annotations

import pipeline_io_bootstrap  # noqa: F401
import json
import sys
from pathlib import Path

import io_fairshare

IMAGE_SUFFIXES = {".tif", ".tiff", ".png", ".jpg", ".jpeg"}


def _is_image(path: Path) -> bool:
    lower = path.name.lower()
    if ".ome." in lower:
        return True
    return path.suffix.lower() in IMAGE_SUFFIXES


def image_metadata(path: Path) -> dict:
    """Return metadata dict for one image path."""
    meta: dict = {
        "width": None,
        "height": None,
        "channels": 1,
        "format": path.suffix.lower().lstrip(".") or "unknown",
    }
    suf = path.suffix.lower()
    try:
        if suf in (".tif", ".tiff") or ".ome." in path.name.lower():
            import tifffile

            io_fairshare.account_nas_open(path)
            with tifffile.TiffFile(path) as tif:
                page = tif.pages[0]
                shape = page.shape
                if len(shape) >= 2:
                    meta["height"] = int(shape[-2])
                    meta["width"] = int(shape[-1])
                    if len(shape) > 2:
                        meta["channels"] = int(shape[0]) if shape[0] < 16 else 1
        else:
            import cv2

            img = cv2.imread(str(path), cv2.IMREAD_UNCHANGED)
            if img is not None:
                if img.ndim == 2:
                    meta["height"], meta["width"] = int(img.shape[0]), int(img.shape[1])
                else:
                    meta["height"], meta["width"] = int(img.shape[0]), int(img.shape[1])
                    meta["channels"] = int(img.shape[2]) if img.ndim == 3 else 1
    except Exception as exc:
        meta["error"] = str(exc)
    return meta


def main() -> None:
    if len(sys.argv) < 2:
        print("[]")
        return
    paths = sys.argv[1:]
    out = []
    for raw in paths:
        p = Path(raw)
        entry = {"path": str(p), "metadata": image_metadata(p) if p.is_file() else {}}
        out.append(entry)
    print(json.dumps(out))


if __name__ == "__main__":
    main()
