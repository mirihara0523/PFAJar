"""File index matching and quality checks (mirrors js/file_index.js)."""

from __future__ import annotations

from typing import Any


def compare_dimensions(
    records_by_role: dict[str, dict[str, Any]], slice_id: str
) -> list[dict[str, str]]:
    issues: list[dict[str, str]] = []
    dims: dict[str, dict[str, int]] = {}
    for role, rec in records_by_role.items():
        meta = rec.get("metadata") or {}
        w, h = meta.get("width"), meta.get("height")
        if w and h:
            dims[role] = {"width": int(w), "height": int(h)}
    roles = list(dims.keys())
    if len(roles) < 2:
        return issues
    base_role, base = roles[0], dims[roles[0]]
    for other_role in roles[1:]:
        other = dims[other_role]
        bw, bh = base["width"], base["height"]
        ow, oh = other["width"], other["height"]
        base_ar = bw / bh
        other_ar = ow / oh
        if abs(base_ar - other_ar) < 0.02 and (bw != ow or bh != oh):
            issues.append(
                {
                    "sliceId": slice_id,
                    "code": "resolution_mismatch",
                    "message": (
                        f"{base_role} and {other_role} for {slice_id} differ in resolution "
                        f"({bw}×{bh} vs {ow}×{oh})."
                    ),
                }
            )
        if abs(base_ar - 1 / other_ar) < 0.05 and abs(base_ar - other_ar) > 0.15:
            issues.append(
                {
                    "sliceId": slice_id,
                    "code": "orientation_swap",
                    "message": (
                        f"Possible orientation mismatch for {slice_id}: "
                        f"{base_role} {bw}×{bh} vs {other_role} {ow}×{oh}."
                    ),
                }
            )
    return issues


def compute_match_report(
    index: dict[str, Any],
    active_roles: list[str] | None = None,
) -> dict[str, Any]:
    active_roles = active_roles or ["dapi", "max", "slices"]
    by_slice: dict[str, dict[str, Any]] = {}
    orphans_by_role: dict[str, list[str]] = {r: [] for r in active_roles}

    for rec in index.get("files", []):
        role = rec.get("role")
        sid = rec.get("sliceId")
        if role not in active_roles or not sid:
            continue
        entry = by_slice.setdefault(sid, {"sliceId": sid, "roles": {}})
        entry["roles"][role] = rec

    matched: list[str] = []
    quality_issues: list[dict[str, str]] = []
    for sid in sorted(by_slice.keys()):
        present = [r for r in active_roles if r in by_slice[sid]["roles"]]
        if len(present) >= 2:
            matched.append(sid)
            quality_issues.extend(compare_dimensions(by_slice[sid]["roles"], sid))
        elif len(present) == 1:
            orphans_by_role[present[0]].append(sid)

    pairwise: dict[str, int] = {}
    for i, a in enumerate(active_roles):
        for b in active_roles[i + 1 :]:
            key = f"{a}+{b}"
            pairwise[key] = sum(
                1 for sid in matched if a in by_slice[sid]["roles"] and b in by_slice[sid]["roles"]
            )

    return {
        "matchedSliceIds": matched,
        "orphansByRole": orphans_by_role,
        "pairwiseCounts": pairwise,
        "qualityIssues": quality_issues,
        "bySlice": by_slice,
    }
