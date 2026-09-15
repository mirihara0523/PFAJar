"""Collate counting results into a single experiment-wide summary.

This reads one or more ``count_results.csv`` files produced by ``count.py`` and
sums per-region detection counts (and region areas) across every section and
project found under the input directory.

Input (`-i`):
    A directory. Every ``count_results.csv`` found anywhere beneath it (one per
    project count leaf in batch mode, or a single project's count leaf in the
    standalone Collate page) is aggregated.

The legacy Tkinter GUI and the old semicolon-delimited "objects" CSV ingestion
were removed -- they predated the modern ``count_results.csv`` format and the
.masonjar project model.
"""

import pipeline_io_bootstrap  # noqa: F401
import argparse
import csv
import pickle
from pathlib import Path


def _norm_acronyms(raw: str) -> set[str]:
    """Parse an optional region filter (comma/semicolon separated acronyms)."""
    if not raw:
        return set()
    tokens = raw.replace(";", ",").split(",")
    return {t.strip() for t in tokens if t.strip()}


def _read_totals_block(csv_path: Path) -> dict[str, tuple[int, int]]:
    """Return {acronym: (count, area)} from the Totals block of a count CSV.

    count_results.csv layout (per count.py):
        <section blocks ...>
        Totals
        Region Acronym, Region Name, Count, Area (px)
        <acronym>, <name>, <count>, <area>
        ...
        (blank)
        Colocalization Matrix (by Section)
        ...
    """
    out: dict[str, tuple[int, int]] = {}
    in_totals = False
    header_seen = False
    with open(csv_path, newline="") as f:
        for row in csv.reader(f):
            if not row:
                # Blank line ends the Totals block.
                if in_totals and header_seen:
                    break
                continue
            first = row[0].strip()
            if first == "Totals":
                in_totals = True
                header_seen = False
                continue
            if not in_totals:
                continue
            if first == "Region Acronym":
                header_seen = True
                continue
            if first.startswith("Colocalization"):
                break
            if not header_seen:
                continue
            acronym = first
            try:
                count = int(row[2]) if len(row) > 2 and row[2] != "" else 0
            except ValueError:
                count = 0
            try:
                area = int(row[3]) if len(row) > 3 and row[3] != "" else 0
            except ValueError:
                area = 0
            prev = out.get(acronym, (0, 0))
            out[acronym] = (prev[0] + count, prev[1] + area)
    return out


def collate_counts(input_dir: str, structures_path: str, output_dir: str, region_filter: str):
    input_path = Path(input_dir.strip())
    out_path = Path(output_dir.strip())
    out_path.mkdir(parents=True, exist_ok=True)

    with open(structures_path.strip(), "rb") as f:
        regions = pickle.load(f)
    acronym_to_id = {v["acronym"]: k for k, v in regions.items()}

    wanted = _norm_acronyms(region_filter)

    csv_files = sorted(input_path.rglob("count_results.csv"))
    print(f"Found {len(csv_files)} count_results.csv file(s) under {input_path}", flush=True)
    if not csv_files:
        print("No count_results.csv files found to collate.", flush=True)
        print("Done!", flush=True)
        raise SystemExit(1)

    totals: dict[str, list[int]] = {}
    for csv_path in csv_files:
        print(f"Collating {csv_path}...", flush=True)
        block = _read_totals_block(csv_path)
        for acronym, (count, area) in block.items():
            if wanted and acronym not in wanted:
                continue
            agg = totals.setdefault(acronym, [0, 0])
            agg[0] += count
            agg[1] += area

    output_file = out_path / "collated_results.csv"
    with open(output_file, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["Region Acronym", "Region Name", "Total Count", "Total Area (px)"])
        for acronym in sorted(totals):
            region_id = acronym_to_id.get(acronym)
            region_name = (
                regions[region_id]["name"]
                if region_id is not None and region_id in regions
                else "Unknown"
            )
            count, area = totals[acronym]
            writer.writerow([acronym, region_name, count, area])

    print(f"Wrote {output_file}", flush=True)
    print("Done!", flush=True)

    from run_manifest import write_run_manifest

    write_run_manifest(
        str(out_path),
        {
            "step": "collate",
            "input": str(input_path),
            "structures": structures_path.strip(),
            "output_dir": str(out_path),
            "sources": len(csv_files),
            "regions": sorted(totals.keys()),
        },
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Collate count results across sections/projects")
    parser.add_argument("-o", "--output", help="output directory", default="")
    parser.add_argument(
        "-i", "--input", help="directory containing count_results.csv file(s)", default=""
    )
    parser.add_argument(
        "-r", "--regions", help="optional region acronym filter (comma separated)", default=""
    )
    parser.add_argument("-s", "--structures", help="structure_map.pkl path", default="")
    parser.add_argument(
        "-g",
        "--graphical",
        help="deprecated/ignored (legacy Tk GUI removed)",
        default="False",
    )
    args = parser.parse_args()

    print("2", flush=True)
    collate_counts(args.input, args.structures, args.output, args.regions)
