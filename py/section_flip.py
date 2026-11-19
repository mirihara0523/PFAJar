"""Transactional horizontal section reflection, with retained originals and recovery journal.

Never touches source CZI files. Stages every replacement before changing originals;
unknown slice-associated data formats are refused instead of partially transformed.
"""
from pathlib import Path
import hashlib
import json
import os
import pickle
import shutil
import time
import uuid
import numpy as np
from detect_live_core import windows_io_path, atomic_write, write_json
from section_policy import policy_path, read_settings, file_id


class DetectionResult:
    """Data-only compatible pickle record; no inference/model import for flipping."""
    pass


class _PredictionReader(pickle.Unpickler):
    def find_class(self, module, name):
        if name == 'DetectionResult' and module in ('__main__', 'find_neurons', 'section_flip'):
            return DetectionResult
        return super().find_class(module, name)


def _root(root):
    root = Path(root).resolve(strict=True)
    if not (root / '.masonjar').is_dir():
        raise ValueError('Open a PFA Jar project with .masonjar metadata to flip sections.')
    return root


def _disk(root, relative):
    candidate = (root / relative).resolve()
    if not candidate.is_relative_to(root):
        raise ValueError('Section flip path escaped the project.')
    return windows_io_path(candidate)


def inventory(root, sid):
    """Exact filename identity, never prefix matching (s1 must not match s10)."""
    if not sid or any(char in sid for char in '/\\') or sid in ('.', '..'):
        raise ValueError('Invalid section ID')
    for project_file in list(root.glob('*.masonjar')) + list(root.glob('*.pfajar')):
        if project_file.is_file():
            project = json.loads(project_file.read_text(encoding='utf-8-sig'))
            for role, folder in project.get('roles', {}).items():
                if role in ('original_scans', 'dapi', 'slices', 'max', 'predictions') and folder:
                    resolved = (root / folder).resolve()
                    if not resolved.is_relative_to(root / 'data'):
                        raise ValueError('Section flip requires project image/annotation roles under data/: ' + role)
    result = []
    for folder in (root / 'data', root / '.masonjar' / 'tissue_cleanup_masks', root / '.masonjar' / 'tissue_cleanup_draft' / 'masks'):
        if not folder.is_dir():
            continue
        for directory, dirs, names in os.walk(windows_io_path(folder), followlinks=False):
            if Path(directory).name == '.masonjar':
                dirs[:] = [name for name in dirs if name == 'annotation_full']
            else:
                dirs[:] = [name for name in dirs if (not name.startswith('.') or name == '.masonjar') and name.lower() not in ('__pycache__', 'logs', 'backup', 'backups', '_backup')]
            for name in names:
                path = Path(directory) / name
                stem = file_id(name)
                preview = path.parent.name == '_previews' and Path(name).stem.startswith(sid + '_')
                if stem != sid and not preview:
                    continue
                if path.is_symlink():
                    raise ValueError('Symbolic link is not supported for section flipping: ' + str(path))
                # Windows extended paths are normalized before relative conversion.
                normal = Path(os.path.abspath(str(path)))
                if os.name == 'nt' and str(normal).startswith('\\\\?\\'):
                    text = str(normal)[4:]
                    normal = Path('\\\\' + text[4:] if text.startswith('UNC\\') else text)
                rel = normal.relative_to(root).as_posix()
                ext = path.suffix.lower()
                if ext in ('.png', '.jpg', '.jpeg', '.tif', '.tiff'):
                    kind = 'image'
                elif ext == '.pkl' and (name.lower().startswith(('annotation_', 'annotations_', 'predictions_')) or path.parent.name == 'annotation_full'):
                    kind = 'prediction' if name.lower().startswith('predictions_') else 'annotation'
                elif ext in ('.json', '.csv'):
                    # Scalar parameters / counts are orientation independent.
                    continue
                else:
                    raise ValueError('Unsupported section-associated file; flip cancelled: ' + rel)
                result.append({'path': rel, 'kind': kind, 'size': path.stat().st_size, 'mtime_ns': path.stat().st_mtime_ns})
    return sorted(result, key=lambda row: row['path'])


def plan_flip(bundle_root, sid):
    root = _root(bundle_root)
    states = read_settings(root, root)
    files = inventory(root, sid)
    if not files:
        raise ValueError('No matching project section files found.')
    payload = {'slice_id': sid, 'files': files, 'sections': states, 'bytes': sum(row['size'] for row in files)}
    payload['signature'] = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
    return payload


def _flip_file(source, target, kind):
    if kind == 'annotation':
        with source.open('rb') as stream:
            array = pickle.load(stream)
        if not isinstance(array, np.ndarray) or array.ndim != 2:
            raise ValueError('Annotation must be a 2D label array: ' + str(source))
        with target.open('wb') as stream:
            pickle.dump(np.ascontiguousarray(array[:, ::-1]), stream, protocol=pickle.HIGHEST_PROTOCOL)
        return
    if kind == 'prediction':
        with source.open('rb') as stream:
            predictions = _PredictionReader(stream).load()
        if not isinstance(predictions, list):
            raise ValueError('Unsupported prediction format: ' + str(source))
        for record in predictions:
            if not isinstance(record, DetectionResult) or len(record.image_dimensions) != 2:
                raise ValueError('Unsupported prediction record: ' + str(source))
            width = int(record.image_dimensions[1])
            boxes = np.asarray(record.boxes)
            if boxes.size:
                if boxes.ndim != 2 or boxes.shape[1] != 4 or not np.all(np.isfinite(boxes)):
                    raise ValueError('Invalid prediction boxes: ' + str(source))
                reflected = boxes.copy()
                # Preserve pixel-centre coordinate semantics used by Count Brain.
                reflected[:, 0], reflected[:, 2] = width - 1 - boxes[:, 2], width - 1 - boxes[:, 0]
                record.boxes = reflected.tolist() if isinstance(record.boxes, list) else reflected
        with target.open('wb') as stream:
            pickle.dump(predictions, stream, protocol=pickle.HIGHEST_PROTOCOL)
        return
    if source.suffix.lower() in ('.tif', '.tiff'):
        import tifffile
        with tifffile.TiffFile(source) as reader, tifffile.TiffWriter(target, bigtiff=True) as writer:
            for page in reader.pages:
                array = page.asarray()
                axes = page.axes
                if 'X' not in axes or len(axes) != array.ndim:
                    raise ValueError('TIFF has ambiguous spatial axes: ' + str(source))
                writer.write(np.ascontiguousarray(np.flip(array, axes.index('X'))), photometric=page.photometric, metadata=None, contiguous=True)
        return
    import cv2
    array = cv2.imdecode(np.fromfile(source, dtype=np.uint8), cv2.IMREAD_UNCHANGED)
    if array is None:
        raise ValueError('Could not read image: ' + str(source))
    ok, encoded = cv2.imencode(source.suffix, np.ascontiguousarray(array[:, ::-1]))
    if not ok:
        raise ValueError('Could not encode reflected image: ' + str(source))
    encoded.tofile(target)


def recover_flip(bundle_root):
    root = _root(bundle_root)
    pending = root / '.masonjar' / 'section_flip_pending.json'
    if not windows_io_path(pending).exists():
        return False
    journal = json.loads(windows_io_path(pending).read_text(encoding='utf-8'))
    for row in journal['targets']:
        target = _disk(root, row['path'])
        backup = _disk(root, row['backup'])
        if row['existed']:
            atomic_write(target, lambda stream, b=backup: _copy_into(b, stream))
        elif target.exists():
            target.unlink()
    windows_io_path(pending).unlink()
    return True


def _copy_into(source, stream):
    with source.open('rb') as original:
        shutil.copyfileobj(original, stream, length=4 * 1024 * 1024)


def apply_flip(bundle_root, sid, expected_plan, report=lambda *args: None):
    root = _root(bundle_root)
    plan = plan_flip(root, sid)
    if plan != expected_plan:
        raise ValueError('Section files or settings changed after review. Review the flip again.')
    backup_rel = '.masonjar/section_flip_backups/' + time.strftime('%Y%m%d-%H%M%S-') + uuid.uuid4().hex
    backup = _disk(root, backup_rel)
    backup.mkdir(parents=True)
    rows = list(plan['files'])
    settings = {key: dict(value) for key, value in plan['sections'].items()}
    settings.setdefault(sid, {})['flip_x'] = not settings.get(sid, {}).get('flip_x', False)
    policy_rel = policy_path(root, root).relative_to(root).as_posix()
    history_rel = '.masonjar/geometry_history.jsonl'
    rows += [{'path': policy_rel, 'kind': 'policy'}, {'path': history_rel, 'kind': 'history'}]
    journal = {'version': 1, 'slice_id': sid, 'targets': []}
    for index, row in enumerate(rows):
        rel = row['path']
        source = _disk(root, rel)
        original_rel = backup_rel + '/originals/' + rel
        original = _disk(root, original_rel)
        staged = _disk(root, backup_rel + '/staged/' + rel)
        staged.parent.mkdir(parents=True, exist_ok=True)
        existed = source.exists()
        if existed:
            original.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, original)
        journal['targets'].append({'path': rel, 'backup': original_rel, 'existed': existed})
        report('flip_staging', index, len(rows))
        if row['kind'] == 'policy':
            write_json(staged, {'version': 1, 'sections': settings})
        elif row['kind'] == 'history':
            old = original.read_text(encoding='utf-8') if existed else ''
            events = [{'kind': 'file', 'ok': True, 'file': item['path'], 'ops': ['flipX'], 'source': 'section-order', 'at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())} for item in plan['files'] if item['kind'] == 'image']
            staged.write_text(old + ('\n' if old and not old.endswith('\n') else '') + ''.join(json.dumps(event) + '\n' for event in events), encoding='utf-8')
        else:
            _flip_file(original, staged, row['kind'])
    # Recheck after potentially long staging; no original has been changed yet.
    if plan_flip(root, sid) != plan:
        raise ValueError('Section changed while preparing the flip. Nothing was replaced.')
    write_json(backup / 'journal.json', journal)
    pending = root / '.masonjar' / 'section_flip_pending.json'
    if windows_io_path(pending).exists():
        raise ValueError('Another section flip needs recovery before applying this change.')
    # Atomic publication: a hard stop cannot leave a truncated recovery journal.
    # Electron's maintenance gate serializes all supported application writers.
    write_json(pending, journal)
    try:
        for index, row in enumerate(rows):
            report('flip_commit', index, len(rows))
            os.replace(_disk(root, backup_rel + '/staged/' + row['path']), _disk(root, row['path']))
        windows_io_path(pending).unlink()
    except BaseException:
        recover_flip(root)
        raise
    report('flip_complete', len(rows), len(rows))
    return {'sections': settings, 'backup': str(root / backup_rel), 'files': len(plan['files'])}
