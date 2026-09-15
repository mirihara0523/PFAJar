"""Full-series orientation audit for geometry repair wizard."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pipeline_io_bootstrap  # noqa: F401

from apply_geometry import compose_ops_from_spec
from czi_common import CANONICAL_REL, emit_log, emit_result, load_import_config
from geometry_orientation_match import downsample_plane, probe_slice_channels, read_preview_plane


def list_channel_previews(bundle_root: Path, slice_id: str) -> list[tuple[str, str]]:
    prev_dir = bundle_root / CANONICAL_REL["previews"]
    out: list[tuple[str, str]] = []
    if not prev_dir.is_dir():
        return out
    prefix = f"{slice_id}_"
    for p in sorted(prev_dir.glob(f"{slice_id}_*.png")):
        if not p.is_file():
            continue
        branch = p.stem[len(slice_id) + 1 :]
        try:
            rel = p.relative_to(bundle_root)
        except ValueError:
            rel = Path(p.name)
        out.append((branch, str(rel).replace("\\", "/")))
    return out


def default_reference_branch(cfg: dict) -> str:
    role = str(cfg.get("primary_signal_role") or "signal_somata")
    if role.startswith("other:"):
        return role.split(":", 1)[1]
    mapping = {
        "signal_somata": "somata",
        "signal_nuclei": "nuclei",
        "signal_axons": "axons",
    }
    return mapping.get(role, "somata")


def pending_ops_for_slice(geometry: dict, slice_id: str) -> list[str]:
    spec = geometry.get(slice_id) or {}
    ops = compose_ops_from_spec(spec)
    out: list[str] = []
    for op, val in ops:
        if op == "rotate" and val == 90:
            out.append("rot90")
        elif op == "rotate" and val == 180:
            out.extend(["rot90", "rot90"])
        elif op == "rotate" and val == 270:
            out.extend(["rot90", "rot90", "rot90"])
        elif op == "flip_x":
            out.append("flipX")
        elif op == "flip_y":
            out.append("flipY")
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="Probe slice orientation fingerprints")
    parser.add_argument("-b", "--bundle", required=True)
    parser.add_argument("-j", "--json", required=True)
    args = parser.parse_args()
    bundle_root = Path(args.bundle.strip()).resolve()
    try:
        cfg = load_import_config(args.json)
    except FileNotFoundError as exc:
        emit_result({"ok": False, "error": str(exc)})
        return 1

    slice_ids = list(cfg.get("slice_ids") or [])
    if not slice_ids:
        order = cfg.get("slice_order") or []
        slice_ids = [str(e.get("sliceId") or "") for e in order if e.get("sliceId")]
    if not slice_ids:
        emit_result({"ok": False, "error": "No slice_ids in probe config"})
        return 1

    geometry = cfg.get("geometry") or {}
    reference_branch = str(cfg.get("reference_branch") or default_reference_branch(cfg))
    per_branch_ref_slice: dict[str, str] = dict(cfg.get("per_branch_reference_slice") or {})

    total = len(slice_ids)
    print(total, flush=True)
    emit_log(f"Orient probe: {total} section(s), reference branch {reference_branch}")

    per_branch_reference_planes: dict[str, object] = {}
    for branch, ref_sid in per_branch_ref_slice.items():
        for b, rel in list_channel_previews(bundle_root, ref_sid):
            if b == branch:
                plane = read_preview_plane(bundle_root / rel)
                if plane is not None:
                    per_branch_reference_planes[branch] = plane
                break

    slices_out: list[dict] = []
    ok_count = 0
    review_count = 0
    auto_count = 0

    for i, slice_id in enumerate(slice_ids):
        channel_paths = list_channel_previews(bundle_root, slice_id)
        pending = pending_ops_for_slice(geometry, slice_id)
        result = probe_slice_channels(
            bundle_root,
            slice_id,
            channel_paths,
            reference_branch,
            pending,
            per_branch_reference_planes,
        )
        slices_out.append(result)
        if result.get("issue") == "ok":
            ok_count += 1
        elif result.get("needs_manual_review"):
            review_count += 1
        elif result.get("auto_repairable"):
            auto_count += 1
        ch_names = ",".join(c["branch"] for c in result.get("channels") or [])
        emit_log(
            f"orient_check [{i + 1}/{total}] {slice_id} — channels: {ch_names or '(none)'}; "
            f"structural_confidence={result.get('structural_confidence')}; issue={result.get('issue')}",
        )
        print(f"Orient check [{i + 1}/{total}] {slice_id}", flush=True)

    payload = {
        "ok": True,
        "slices": slices_out,
        "reference_branch": reference_branch,
        "per_branch_reference_slice": per_branch_ref_slice,
        "summary": {
            "total": total,
            "ok": ok_count,
            "need_review": review_count,
            "auto_repairable": auto_count,
        },
    }
    emit_result(payload)
    print("Done!", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
