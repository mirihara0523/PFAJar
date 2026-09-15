"""Low-resolution background channel discovery for Viewer/Editor (adjust)."""

from __future__ import annotations

from pathlib import Path


def resolve_previews_dir(images_dir: Path) -> Path | None:
    """Return ``data/counting/_previews`` sibling of ``00_dapi`` if it exists."""
    candidate = Path(images_dir).parent / "_previews"
    return candidate if candidate.is_dir() else None


def _display_name_from_suffix(suffix: str) -> str:
    return suffix.replace("_", " ").title()


def build_lowres_channel_index(
    images_dir: Path,
    slice_ids: list[str],
    previews_dir: Path | None = None,
) -> dict[str, list[tuple[str, Path]]]:
    """Build Viewer-session background choices for every supplied slice.

    ``_previews`` is commonly on a network share.  Enumerating its full
    contents once per Previous/Next transition dominated Viewer navigation.
    The Viewer does not create preview files, so one immutable index per
    session preserves discovery behavior while avoiding repeated directory
    walks.  A slice without previews retains the existing DAPI PNG fallback.
    """
    images_dir = Path(images_dir)
    if previews_dir is None:
        previews_dir = resolve_previews_dir(images_dir)
    ordered_ids = list(dict.fromkeys(str(slice_id) for slice_id in slice_ids))
    index: dict[str, list[tuple[str, Path]]] = {slice_id: [] for slice_id in ordered_ids}
    prefixes = sorted(
        ((f"{slice_id}_", slice_id) for slice_id in ordered_ids),
        key=lambda item: len(item[0]),
        reverse=True,
    )

    if previews_dir is not None and previews_dir.is_dir():
        for entry in sorted(previews_dir.iterdir()):
            if not entry.is_file() or entry.suffix.lower() != ".png":
                continue
            for prefix, slice_id in prefixes:
                if not entry.name.startswith(prefix):
                    continue
                suffix = entry.stem[len(prefix) :]
                if suffix:
                    index[slice_id].append((_display_name_from_suffix(suffix), entry))
                break

    missing = [slice_id for slice_id in ordered_ids if not index[slice_id]]
    if not missing or not images_dir.is_dir():
        return index

    # One DAPI directory walk also covers dotted acquisition file names when
    # the project slice ID is a shorter prefix.
    dapi_entries = sorted(
        entry
        for entry in images_dir.iterdir()
        if entry.is_file() and entry.suffix.lower() == ".png"
    )
    for slice_id in missing:
        exact_name = f"{slice_id}.png"
        candidate = next(
            (
                entry
                for entry in dapi_entries
                if entry.name == exact_name or entry.stem.startswith(f"{slice_id}.")
            ),
            None,
        )
        if candidate is not None:
            index[slice_id].append(("DAPI (pipeline)", candidate))
    return index


def lowres_channels_for_slice(
    images_dir: Path,
    slice_id: str,
    previews_dir: Path | None = None,
    preview_index: dict[str, list[tuple[str, Path]]] | None = None,
) -> list[tuple[str, Path]]:
    """Return ``[(display_name, path), ...]`` from ``_previews/{sliceId}_*.png``.

    When ``_previews`` is missing or has no matches, fall back to
    ``00_dapi/{sliceId}.png`` as ``DAPI (pipeline)`` when that file exists.
    """
    images_dir = Path(images_dir)
    if previews_dir is None:
        previews_dir = resolve_previews_dir(images_dir)

    channels: list[tuple[str, Path]] = []
    if preview_index is not None:
        return list(preview_index.get(str(slice_id), []))

    if previews_dir is not None and previews_dir.is_dir():
        prefix = f"{slice_id}_"
        for entry in sorted(previews_dir.iterdir()):
            if not entry.is_file():
                continue
            name = entry.name
            if not name.lower().endswith(".png"):
                continue
            if not name.startswith(prefix):
                continue
            suffix = entry.stem[len(slice_id) + 1 :]
            if not suffix:
                continue
            channels.append((_display_name_from_suffix(suffix), entry))

    if not channels:
        dapi_path = images_dir / f"{slice_id}.png"
        if dapi_path.is_file():
            channels.append(("DAPI (pipeline)", dapi_path))
        else:
            # Project slice IDs can be a short prefix while imported DAPI
            # files retain dotted acquisition names (e.g. 202607.M554...).
            candidates = sorted(
                p for p in images_dir.iterdir()
                if p.is_file() and p.suffix.lower() == ".png"
                and (p.stem == slice_id or p.stem.startswith(f"{slice_id}."))
            )
            if candidates:
                channels.append(("DAPI (pipeline)", candidates[0]))

    return channels
