from tkinter import filedialog
import pipeline_io_bootstrap  # noqa: F401
import os
import tifffile as tiff
from pathlib import Path
import tempfile
import tkinter as tk
import numpy as np
import argparse
import cv2

def _stream_max_first_axis(file):
    """Return a first-axis MAX projection without materializing the stack.

    TIFF pages are read one at a time. The caller only uses this for stacks
    whose first axis is the selected projection axis; unusual axis layouts
    retain the legacy ndarray path below.
    """
    projected = None
    with tiff.TiffFile(file) as tif:
        for page in tif.pages:
            plane = np.asarray(page.asarray())
            if plane.ndim != 2:
                raise ValueError(f"Expected 2-D TIFF pages, got shape {plane.shape}")
            if projected is None:
                projected = plane.copy()
            elif projected.shape != plane.shape:
                raise ValueError("TIFF pages have inconsistent dimensions")
            else:
                np.maximum(projected, plane, out=projected)
    if projected is None:
        raise ValueError("TIFF has no image pages")
    return projected


def process_file(file, outputDirectory, topHat=False, dendrite=False):
    # Update current file
    try:
        print(f"Processing {file}", flush=True)
        import perf_log
        perf_log.perf_memory("max.before_file")
        # A paged TIFF with Z as its first (and smallest) axis can be reduced
        # one page at a time. This avoids retaining the full Z-stack and the
        # temporary array created by np.max, while preserving the old axis
        # selection for channel-last or unusual TIFF layouts.
        img = None
        with tiff.TiffFile(file) as tif:
            page_count = len(tif.pages)
        if page_count > 1:
            first = tiff.imread(file, key=0)
            with tiff.TiffFile(file) as tif:
                shape = (page_count,) + tuple(first.shape)
            if first.ndim == 2 and page_count <= min(first.shape):
                projected = _stream_max_first_axis(file)
            else:
                img = tiff.imread(file)
        else:
            img = tiff.imread(file)
        # Get filename stem
        stem = Path(file).stem
        if img is None:
            pass
        elif img.ndim == 2:
            # Single-plane image (e.g. sparse-Z counterstain): nothing to
            # project. np.argmin over (H, W) would otherwise collapse a spatial
            # axis and produce a 1-D line.
            projected = img
        elif img.ndim == 3:
            # Project over the smallest axis (Z or channel), keeping H x W.
            channel_dim = int(np.argmin(img.shape))
            projected = np.max(img, axis=channel_dim)
        else:
            raise ValueError(
                f"Unsupported image with {img.ndim} dimensions (shape {img.shape})"
            )
        # Save the processed image
        destination = Path(outputDirectory) / f"{stem}.tif"
        # Encode beside the destination, then replace only after a successful
        # write. A failed encoder must not damage an existing result.
        with tempfile.NamedTemporaryFile(dir=outputDirectory, suffix=".tif", delete=False) as tmp:
            temporary = Path(tmp.name)
        try:
            if not cv2.imwrite(str(temporary), projected):
                raise OSError(f"Could not save {destination.name}")
            os.replace(temporary, destination)
        finally:
            temporary.unlink(missing_ok=True)
        perf_log.perf_memory("max.after_file")
        return True
    except Exception as e:
        print(f"Failed to process {file}. Error: {e}", flush=True)
        return False


def main(argv=None):
    parser = argparse.ArgumentParser(description="Process z-stack images")
    parser.add_argument(
        "-o",
        "--output",
        help="output directory, only use if graphical false",
        default="",
    )
    parser.add_argument(
        "-i", "--input", help="input directory, only use if graphical false", default=""
    )
    parser.add_argument(
        "-g", "--graphical", help="provides prompts when true", default=True
    )
    parser.add_argument(
        "-d", "--dendrite", help="remove dendrites when true", default=False
    )
    parser.add_argument(
        "-t", "--tophat", help="apply tophat filter when true", default=False
    )
    args = parser.parse_args(argv)

    if args.graphical == True:
        root = tk.Tk()
        root.withdraw()

        inputDirectory = filedialog.askdirectory(title="Select input directory")
        outputDirectory = filedialog.askdirectory(title="Select output directory")
    else:
        inputDirectory = args.input.strip()
        outputDirectory = args.output.strip()

    inputDirectory = str(Path(inputDirectory).resolve())
    outputDirectory = str(Path(outputDirectory).resolve())
    Path(outputDirectory).mkdir(parents=True, exist_ok=True)
    # Only project real TIFF files; skip subdirectories (.masonjar meta, run
    # leaves), run_manifest.json, and any non-image entries.
    files = sorted(
        f
        for f in os.listdir(inputDirectory)
        if (Path(inputDirectory) / f).is_file() and f.lower().endswith((".tif", ".tiff"))
    )
    if len(files) == 0:
        print(1, flush=True)
        print("No TIFF files found in input directory", flush=True)
        return 1
    # Pass number of files to electron
    print(len(files), flush=True)
    # Distinct extensions (sample.tif/sample.tiff) can still map to the same
    # output. Reject the run before writing rather than silently overwrite.
    names = [f"{Path(f).stem}.tif".casefold() for f in files]
    if len(names) != len(set(names)):
        print("MAX_OUTPUT_COLLISION: input files map to the same output name", flush=True)
        return 1
    written = 0
    failed = []
    for file in files:
        if process_file(str(Path(inputDirectory) / file), outputDirectory, args.tophat, args.dendrite):
            written += 1
        else:
            failed.append(file)
        print(f"MAX_FILE_DONE:{written + len(failed)}/{len(files)}", flush=True)

    if written == 0:
        # Inputs existed but none projected: a failed run, not a silent success.
        print(f"MAX_NO_OUTPUT: 0 of {len(files)} files projected.", flush=True)
    elif failed:
        print(f"MAX_PARTIAL_FAILURE: {len(failed)} of {len(files)} files failed", flush=True)
    from run_manifest import write_run_manifest

    write_run_manifest(
        outputDirectory,
        {
            "step": "max",
            "input_dir": inputDirectory,
            "input_files": files,
            "ok": not failed,
            "written": written,
            "failed_files": failed,
            "dendrite": bool(args.dendrite),
            "tophat": bool(args.tophat),
        },
    )
    print("Done!", flush=True)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
