"""Batch-polish DAPI preview images: grayscale, tissue mask, uniform background, levels."""

from __future__ import annotations

import pipeline_io_bootstrap  # noqa: F401
import argparse
import shutil
from pathlib import Path

import cv2
import numpy as np
import tifffile as tiff
from slice_index import load_slice_list, slice_id_allowed, slice_stem_from_image_filename
from tissue_mask import isolate_tissue_mask

VALID_EXTENSIONS = {".tif", ".tiff", ".png"}


def load_grayscale_float(path: Path) -> np.ndarray:
    suffix = path.suffix.lower()
    if suffix in {".tif", ".tiff"}:
        img = tiff.imread(str(path))
    else:
        img = cv2.imread(str(path), cv2.IMREAD_UNCHANGED)
        if img is None:
            raise ValueError(f"Could not read {path}")
    if img.ndim == 3:
        if img.shape[2] >= 3:
            img = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY if img.shape[2] == 3 else cv2.COLOR_BGRA2GRAY)
        else:
            img = img[..., 0]
    elif img.ndim > 2:
        img = np.max(img, axis=0)
    arr = np.asarray(img)
    gray = arr.astype(np.float64)
    if np.issubdtype(arr.dtype, np.integer):
        gmax = float(np.max(gray)) if gray.size else 255.0
        if gmax <= 255.0:
            gray = gray
        else:
            gray = gray / gmax * 255.0
    elif gray.max() <= 1.0:
        gray = gray * 255.0
    elif gray.max() > 255.0:
        gray = gray * (255.0 / float(gray.max()))
    return np.clip(gray, 0, 255)



def percentile_stretch_to_uint8(
    image: np.ndarray, saturation_pct: float
) -> np.ndarray:
    flat = image.ravel().astype(np.float64)
    lo = np.percentile(flat, saturation_pct)
    hi = np.percentile(flat, 100.0 - saturation_pct)
    if hi <= lo:
        hi = lo + 1.0
    clipped = np.clip(flat, lo, hi)
    scaled = (clipped - lo) / (hi - lo) * 255.0
    return scaled.reshape(image.shape).astype(np.uint8)


def process_image(
    gray: np.ndarray,
    *,
    isolate: bool,
    bg_value: float | None,
    saturation_pct: float,
    use_clahe: bool,
) -> np.ndarray:
    gray_u8 = np.clip(gray, 0, 255).astype(np.uint8)
    if isolate:
        mask = isolate_tissue_mask(gray_u8)
        bg = 15.0 if bg_value is None else float(bg_value)
        out = np.full_like(gray, bg, dtype=np.float64)
        out[mask] = gray[mask]
    else:
        out = gray.copy()

    if use_clahe:
        clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
        work = clahe.apply(np.clip(out, 0, 255).astype(np.uint8)).astype(np.float64)
    else:
        work = out

    return percentile_stretch_to_uint8(work, saturation_pct)


def maybe_backup(
    src: Path,
    backup_dir: Path | None,
    re_backup: bool,
) -> None:
    if backup_dir is None:
        return
    backup_dir.mkdir(parents=True, exist_ok=True)
    dest = backup_dir / src.name
    if dest.exists() and not re_backup:
        return
    shutil.copy2(src, dest)


def list_input_files(input_path: Path) -> list[Path]:
    files = [
        f
        for f in input_path.iterdir()
        if f.is_file() and f.suffix.lower() in VALID_EXTENSIONS
    ]
    files.sort(key=lambda p: p.name.lower())
    return files


def process_file(
    file_path: Path,
    output_path: Path,
    *,
    in_place: bool,
    backup_dir: Path | None,
    re_backup: bool,
    isolate: bool,
    bg_value: float | None,
    saturation_pct: float,
    use_clahe: bool,
) -> None:
    print(f"Processing {file_path.name}", flush=True)
    if in_place and backup_dir is not None:
        maybe_backup(file_path, backup_dir, re_backup)
    gray = load_grayscale_float(file_path)
    result = process_image(
        gray,
        isolate=isolate,
        bg_value=bg_value,
        saturation_pct=saturation_pct,
        use_clahe=use_clahe,
    )
    out_file = output_path / (file_path.stem + ".png")
    cv2.imwrite(str(out_file), result.astype(np.uint8))


def main() -> None:
    parser = argparse.ArgumentParser(description="Clean DAPI preview images")
    parser.add_argument("-i", "--input", help="input directory", default="")
    parser.add_argument("-o", "--output", help="output directory", default="")
    parser.add_argument(
        "--isolate",
        dest="isolate",
        action="store_true",
        help="isolate tissue with classical mask (default)",
    )
    parser.add_argument(
        "--no-isolate",
        dest="isolate",
        action="store_false",
        help="levels-only (no tissue mask)",
    )
    parser.set_defaults(isolate=True)
    parser.add_argument(
        "--bg-value",
        type=float,
        default=None,
        help="fixed background level 0-255 (default: auto from border)",
    )
    parser.add_argument(
        "--saturation",
        type=float,
        default=5.0,
        help="percentile clip at each tail (default 5)",
    )
    parser.add_argument(
        "--clahe",
        action="store_true",
        help="apply CLAHE before final stretch",
    )
    parser.add_argument(
        "--backup-dir",
        default="",
        help="backup originals here before in-place overwrite",
    )
    parser.add_argument(
        "--re-backup",
        action="store_true",
        help="overwrite existing backup copies",
    )
    parser.add_argument(
        "--slice-list",
        default="",
        help="JSON slice id list to process",
    )
    args = parser.parse_args()

    input_path = Path(args.input.strip())
    output_path = Path(args.output.strip())
    output_path.mkdir(parents=True, exist_ok=True)

    backup_dir: Path | None = None
    if args.backup_dir.strip():
        backup_dir = Path(args.backup_dir.strip())

    bg_value = args.bg_value
    if bg_value is not None:
        bg_value = float(np.clip(bg_value, 0, 255))

    allowed = load_slice_list(args.slice_list.strip() or None)
    input_files = list_input_files(input_path)
    if allowed is not None:
        input_files = [
            f
            for f in input_files
            if slice_id_allowed(slice_stem_from_image_filename(f.name), allowed)
        ]

    in_place = input_path.resolve() == output_path.resolve()
    print(f"{len(input_files)}", flush=True)

    for file_path in input_files:
        try:
            process_file(
                file_path,
                output_path,
                in_place=in_place,
                backup_dir=backup_dir if in_place else None,
                re_backup=args.re_backup,
                isolate=args.isolate,
                bg_value=bg_value,
                saturation_pct=float(args.saturation),
                use_clahe=bool(args.clahe),
            )
        except Exception as exc:
            print(f"Failed to process {file_path.name}. Error: {exc}", flush=True)

    print("Done!", flush=True)


if __name__ == "__main__":
    main()
