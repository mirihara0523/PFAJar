"""
DAPI Z-plane ambiguity diagnostic (v2 — crash-isolated) — run on the Windows machine.

Purpose
-------
For a CZI file whose DAPI channel reports "ambiguous_metadata" (i.e.
z_indices_with_data() returns multiple Z candidates instead of exactly one),
this script checks whether the underlying pixel data is genuinely different
across those Z candidates, or blank/duplicated.

v2 change: reading a plane via aicspylibczi can crash the interpreter
natively (access violation) instead of raising a catchable Python
exception. To keep one bad Z from aborting the whole probe, each candidate
Z is now read in its OWN child process (mirrors the app's own
"Isolated CZI child" crash-recovery pattern in czi_extract.py). If a child
crashes, its exit code is recorded and the parent moves on to the next Z.

How to run
----------
Run with the app's own bundled Python (only interpreter with aicspylibczi):

    cd D:\\Claude\\masonjar-7.0.2-MC.1-improving\\py
    C:\\Users\\mirih\\.masonjar\\benv\\Scripts\\python.exe dapi_z_probe.py "<path-to-czi>" --scene 0 --channel 2

Output
------
Per-Z: OK (shape/dtype/min/max/mean/sha256) or CRASHED (exit code) or
READ FAILED (a normal Python exception, with message).

Final verdict considers three outcomes, not two:
  - IDENTICAL: all successfully-read planes hash the same.
  - DIFFERENT: successfully-read planes have different hashes (and are not
    all blank) -> genuinely distinct per-Z data.
  - BLANK: some/all successfully-read planes are all-zero (min=max=0) ->
    those Z's have subblock metadata but no real pixel content, suggesting
    _z_position_has_subblocks() may be detecting placeholder/empty
    subblocks rather than real image data.
  - CRASHED entries are reported separately since they could not be
    compared at all.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path

THIS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(THIS_DIR))


def _child_read_single_z(czi_path: str, scene: int, channel: int, z: int) -> None:
    """Run in a child process: read one plane, print a JSON result line, exit 0."""
    try:
        from aicspylibczi import CziFile
        import czi_common

        czi = CziFile(czi_path)
        plane = czi_common.read_czi_plane(czi, scene, z, channel)
        arr = plane[0] if isinstance(plane, tuple) else plane
        result = {
            "ok": True,
            "shape": list(arr.shape),
            "dtype": str(arr.dtype),
            "min": float(arr.min()),
            "max": float(arr.max()),
            "mean": float(arr.mean()),
            "sha256": hashlib.sha256(arr.tobytes()).hexdigest(),
        }
    except Exception as exc:  # noqa: BLE001
        result = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    # Single JSON line on its own marker so the parent can find it even if
    # aicspylibczi/libCZI printed other noise to stdout/stderr before this.
    print("DAPI_PROBE_RESULT_JSON:" + json.dumps(result))
    sys.stdout.flush()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("czi_path", type=str, nargs="?", help="Path to the CZI file to probe")
    ap.add_argument("--scene", type=int, default=0)
    ap.add_argument("--channel", type=int, default=2)
    ap.add_argument(
        "--single-z", type=int, default=None,
        help=argparse.SUPPRESS,  # internal: used by the child-process re-invocation
    )
    args = ap.parse_args()

    if not args.czi_path:
        print("ERROR: czi_path is required", file=sys.stderr)
        return 2

    if args.single_z is not None:
        # Child mode: just read the one plane and print the JSON result.
        _child_read_single_z(args.czi_path, args.scene, args.channel, args.single_z)
        return 0

    # Parent mode.
    try:
        from aicspylibczi import CziFile
    except ImportError:
        print(
            "ERROR: aicspylibczi is not importable in this Python. Run with:\n"
            r'  C:\Users\mirih\.masonjar\benv\Scripts\python.exe dapi_z_probe.py ...',
            file=sys.stderr,
        )
        raise
    try:
        import czi_common
    except ImportError:
        print(
            "ERROR: could not import czi_common.py. Run this script from inside "
            "the PFA Jar source's py/ folder (next to czi_common.py).",
            file=sys.stderr,
        )
        raise

    czi_path = Path(args.czi_path)
    if not czi_path.is_file():
        print(f"ERROR: file not found: {czi_path}", file=sys.stderr)
        return 2

    print(f"Opening: {czi_path}")
    czi = CziFile(str(czi_path))

    try:
        print(f"Detected channel indices: {czi_common.channel_indices_from_czi(czi)}")
    except Exception as exc:  # noqa: BLE001
        print(f"(could not enumerate channels: {exc})")

    all_z = czi_common.z_indices_from_czi(czi)
    print(f"Full Z range reported by metadata: {all_z}")

    candidates = czi_common.z_indices_with_data(
        czi, args.scene, args.channel, log_sparse=False
    )
    print(f"\nz_indices_with_data(scene={args.scene}, channel={args.channel}) -> {candidates}")
    if len(candidates) <= 1:
        print("Only one (or zero) candidate — not the ambiguous_metadata case. Nothing to compare.")
        return 0

    print(
        f"\n{len(candidates)} Z candidates. Reading each in an isolated child process "
        "so a crash on one Z doesn't stop the rest...\n"
    )

    py_exe = sys.executable
    script_path = str(Path(__file__).resolve())
    outcomes: dict[int, dict] = {}

    for z in candidates:
        print(f"Z={z}: ", end="", flush=True)
        proc = subprocess.run(
            [
                py_exe, script_path, str(czi_path),
                "--scene", str(args.scene),
                "--channel", str(args.channel),
                "--single-z", str(z),
            ],
            capture_output=True,
            text=True,
            timeout=180,
        )
        marker = "DAPI_PROBE_RESULT_JSON:"
        result = None
        for line in (proc.stdout or "").splitlines():
            if line.startswith(marker):
                try:
                    result = json.loads(line[len(marker):])
                except json.JSONDecodeError:
                    pass
                break

        if result is None:
            print(f"CRASHED (child exit code {proc.returncode})")
            if proc.stderr.strip():
                print(f"  stderr tail: {proc.stderr.strip()[-500:]}")
            outcomes[z] = {"ok": False, "crashed": True, "exit_code": proc.returncode}
            continue

        if not result.get("ok"):
            print(f"READ FAILED: {result.get('error')}")
            outcomes[z] = {"ok": False, "crashed": False, "error": result.get("error")}
            continue

        print(
            f"shape={result['shape']} dtype={result['dtype']} "
            f"min={result['min']} max={result['max']} mean={result['mean']:.3f} "
            f"sha256={result['sha256']}"
        )
        outcomes[z] = result

    print("\n--- Summary ---")
    good = {z: r for z, r in outcomes.items() if r.get("ok")}
    crashed = [z for z, r in outcomes.items() if r.get("crashed")]
    failed = [z for z, r in outcomes.items() if not r.get("ok") and not r.get("crashed")]

    if crashed:
        print(f"CRASHED Z's (native interpreter crash while reading): {crashed}")
    if failed:
        print(f"READ-FAILED Z's (Python exception): {failed}")

    if len(good) < 2:
        print("Fewer than 2 successfully-read planes — cannot compare. See crash/failure list above.")
        return 0

    blank_zs = [z for z, r in good.items() if r["min"] == 0 and r["max"] == 0]
    nonblank = {z: r for z, r in good.items() if z not in blank_zs}

    if blank_zs:
        print(
            f"BLANK Z's (subblock metadata present but pixel data is all-zero): {blank_zs} "
            "-> these Z's likely should NOT count as 'has data' for DAPI selection purposes; "
            "this points at a gap in _z_position_has_subblocks()/z_indices_with_data(), which "
            "only checks bbox existence, not whether the tile actually contains non-empty pixels."
        )

    if nonblank:
        hashes = {r["sha256"] for r in nonblank.values()}
        if len(hashes) == 1:
            print(
                f"Non-blank Z's {sorted(nonblank)} are IDENTICAL to each other "
                "(same real content duplicated across those Z's)."
            )
        else:
            print(
                f"Non-blank Z's {sorted(nonblank)} are DIFFERENT from each other "
                "(genuinely distinct real image content at different Z)."
            )
    else:
        print("No non-blank planes were found among the successfully-read Z's.")

    print("\n--- Verdict ---")
    if blank_zs and nonblank and len(nonblank) == 1:
        only_z = next(iter(nonblank))
        print(
            f"Only Z={only_z} has real (non-blank) pixel data; the other candidate Z's "
            "are blank placeholders. This means 'ambiguous_metadata' here is a false "
            "positive from the metadata check (it should have narrowed to a single Z), "
            "not evidence of genuinely duplicated acquisition."
        )
    elif blank_zs and nonblank and len(nonblank) > 1:
        print(
            "Multiple Z's have real data (some identical, check message above) while "
            f"others ({blank_zs}) are blank. The ambiguity is partly a detection gap "
            "(blank Z's shouldn't be candidates) and partly still needs the priority-order "
            "fallback among the real candidates."
        )
    elif not blank_zs and nonblank:
        hashes = {r["sha256"] for r in nonblank.values()}
        if len(hashes) == 1:
            print("IDENTICAL: all candidates carry the same real pixel data. Harmless duplication.")
        else:
            print("DIFFERENT: candidates carry genuinely different real pixel data.")
    else:
        print("Inconclusive — see crash/failure lists above.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
