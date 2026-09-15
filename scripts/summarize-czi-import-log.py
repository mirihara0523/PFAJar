"""Read-only summary of a Mason Jar CZI import log, including isolated retries."""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

START = re.compile(r"^\s*\[(\d+)/(\d+)\]\s+(.+?)\s+role=([^\s]+)\s+file=(.+?)\s+scene=(\d+)\s+ch=(\d+)")
DONE = re.compile(r"^\s*\[(\d+)/(\d+)\]\s+(.+?)\s+([^\s]+)\s+done in\s+([\d.]+)s")
READ = re.compile(r"Reading Z\s+(\d+)/(\d+)\s+\((.+?)\s+ch\s+(\d+)\)")
CHILD_EXIT = re.compile(r"isolated CZI child exited code=([-\d]+)")
EXHAUSTED = re.compile(r"exhausted isolated retry for\s+(.+)$")


def summarize_lines(lines: list[str]) -> dict:
    total = None
    started = {}
    completed = {}
    retries = 0
    child_exit_codes = []
    exhausted = []
    last_read = None
    finished = False
    result_ok = None
    for line_no, raw in enumerate(lines, start=1):
        line = raw.strip()
        match = START.match(line)
        if match:
            ordinal, count, slice_id, role, filename, scene, channel = match.groups()
            total = int(count)
            started[int(ordinal)] = {
                "ordinal": int(ordinal), "slice_id": slice_id, "role": role,
                "file": filename, "scene": int(scene), "channel": int(channel), "line": line_no,
            }
            continue
        match = DONE.match(line)
        if match:
            ordinal, count, slice_id, role, seconds = match.groups()
            total = int(count)
            completed[int(ordinal)] = {
                "ordinal": int(ordinal), "slice_id": slice_id, "role": role,
                "seconds": float(seconds), "line": line_no,
            }
            continue
        match = READ.search(line)
        if match:
            z, z_total, slice_id, channel = match.groups()
            last_read = {"z": int(z), "z_total": int(z_total), "slice_id": slice_id, "channel": int(channel), "line": line_no}
        if "retrying isolated CZI child" in line:
            retries += 1
        match = CHILD_EXIT.search(line)
        if match:
            child_exit_codes.append(int(match.group(1)))
        match = EXHAUSTED.search(line)
        if match:
            exhausted.append(match.group(1))
        if line == "Done!":
            finished = True
        if line.startswith("RESULT:"):
            try:
                result_ok = bool(json.loads(line.split(":", 1)[1]).get("ok"))
            except (json.JSONDecodeError, AttributeError):
                pass
    last_started = started[max(started)] if started else None
    status = "complete" if finished and result_ok is not False else "interrupted"
    if exhausted or result_ok is False:
        status = "completed_with_failures" if finished else "interrupted_with_failures"
    return {
        "total_items": total,
        "completed_items": len(completed),
        "last_completed": completed[max(completed)] if completed else None,
        "last_started": last_started,
        "last_read": last_read,
        "isolated_retries": retries,
        "isolated_child_exit_codes": child_exit_codes,
        "exhausted_items": exhausted,
        "status": status,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("log", type=Path)
    args = parser.parse_args()
    if not args.log.is_file():
        parser.error(f"Log not found: {args.log}")
    print(json.dumps(summarize_lines(args.log.read_text(encoding="utf-8", errors="replace").splitlines()), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
