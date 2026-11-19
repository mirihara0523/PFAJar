"""Shared, fail-closed section eligibility and orientation metadata."""
from pathlib import Path
import json
from detect_live_core import windows_io_path, write_json


def policy_path(location, bundle_root=None):
    if bundle_root:
        return Path(bundle_root) / '.masonjar' / 'section_settings.json'
    start = Path(location).resolve()
    if start.is_file():
        start = start.parent
    ancestors = (start, *start.parents)
    # Annotation leaves also have .masonjar/ (parcellation metadata). Prefer the
    # actual project root, never mistake a leaf's metadata for the project.
    for root in ancestors:
        if any(item.is_file() for pattern in ('*.masonjar', '*.belljar', '*.pfajar') for item in root.glob(pattern)):
            return root / '.masonjar' / 'section_settings.json'
    for root in ancestors:
        meta = root / '.masonjar'
        if any((meta / name).exists() for name in ('section_settings.json', 'section_flip_pending.json', 'slice_order.json')):
            return meta / 'section_settings.json'
    for root in ancestors:
        if (root / '.masonjar').is_dir():
            return root / '.masonjar' / 'section_settings.json'
    return start / '.pfa_section_settings.json'



def read_settings(location, bundle_root=None, *, allow_pending=False):
    target = policy_path(location, bundle_root)
    if not allow_pending and windows_io_path(target.parent / 'section_flip_pending.json').exists():
        raise ValueError('An interrupted section flip needs recovery. Open Section Manager before analysing this project.')
    disk = windows_io_path(target)
    if not disk.exists():
        return {}
    raw = json.loads(disk.read_text(encoding='utf-8'))
    if not isinstance(raw, dict) or raw.get('version') != 1 or not isinstance(raw.get('sections'), dict):
        raise ValueError('Invalid section settings. Restore its backup before analysing this project.')
    sections = raw['sections']
    for sid, state in sections.items():
        if not isinstance(sid, str) or not sid or not isinstance(state, dict):
            raise ValueError('Invalid section settings entry')
        if any(type(state.get(key, default)) is not bool for key, default in [('active', True), ('flip_x', False)]):
            raise ValueError('Section active/flip_x settings must be true or false')
    return sections


def ordered_items(items, location, key=lambda item: item, bundle_root=None):
    """Use the shared section order without changing filename IDs or AP values.

    No saved order preserves each caller's existing default ordering. Unknown new
    sections append naturally after saved IDs; channel subsets retain their order.
    """
    from slice_order import read_order, apply_order
    policy = policy_path(location, bundle_root)
    target = policy.parent / ('slice_order.json' if policy.name == 'section_settings.json' else '.pfa_viewer_slice_order.json')
    order = read_order(windows_io_path(target))
    return apply_order(items, order, key=key) if order else list(items)


def active_ids(ids, location, bundle_root=None):
    states = read_settings(location, bundle_root)
    return ordered_items([sid for sid in ids if states.get(sid, {}).get('active', True)], location, bundle_root=bundle_root)


def file_id(name):
    stem = Path(name).stem
    if stem.lower().endswith('.ome'):
        stem = stem[:-4]
    for prefix in ('Predictions_', 'Annotation_', 'annotations_', 'Label_', 'Composite_', 'BBoxes_'):
        if stem.lower().startswith(prefix.lower()):
            return stem[len(prefix):]
    return stem


def active_files(items, location, key=lambda item: file_id(str(item))):
    states = read_settings(location)
    return ordered_items([item for item in items if states.get(key(item), {}).get('active', True)], location, key=key)


def save_active(location, changes, expected, bundle_root=None):
    states = read_settings(location, bundle_root)
    if states != expected:
        raise ValueError('Section settings changed in another window. Reload and try again.')
    merged = {sid: dict(state) for sid, state in states.items()}
    for sid, active in changes.items():
        if not isinstance(sid, str) or not sid or type(active) is not bool:
            raise ValueError('Invalid section active state')
        merged.setdefault(sid, {})['active'] = active
    write_json(policy_path(location, bundle_root), {'version': 1, 'sections': merged})
    return merged
