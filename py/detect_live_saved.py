"""Self-contained candidate snapshots for navigation without another inference run."""
import hashlib
import json
import math
import re
from detect_live_core import atomic_write, windows_io_path, filter_indices


def file_hash(path):
    digest = hashlib.sha256()
    with windows_io_path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(4 * 1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def store_snapshot(output, state, params, indices, prediction):
    normalized = {key: float(params.get(key, default)) for key, default in
                  [('confidence', .5), ('area', 200), ('eccentricity', .2), ('intensity', 0)]}
    payload = {'schema': 1, 'source': str(state['source']), 'source_stamp': list(state['source_stamp']),
               'dimensions': [state['height'], state['width']], 'records': state['records'],
               'floor': state['floor'], 'inference': state['settings'], 'params': normalized,
               'indices': indices, 'prediction_hash': file_hash(prediction)}
    data = json.dumps(payload, allow_nan=False, separators=(',', ':')).encode('utf-8')
    name = hashlib.sha256(data).hexdigest() + '.json'
    atomic_write(output / '.live_saved' / name, lambda stream: stream.write(data))
    return name, normalized


def restore_snapshot(output, state, entry, prediction):
    """Do not display results from another channel, changed image, or partial save."""
    name = entry.get('snapshot', '')
    if not re.fullmatch(r'[a-f0-9]{64}\.json', name):
        raise ValueError('Saved predictions have no restorable candidate snapshot. Detect once and save to enable automatic loading.')
    data = windows_io_path(output / '.live_saved' / name).read_bytes()
    if hashlib.sha256(data).hexdigest() + '.json' != name:
        raise ValueError('Saved candidate snapshot failed its integrity check.')
    saved = json.loads(data)
    if (saved.get('schema') != 1 or saved.get('source') != str(state['source'])
            or saved.get('source_stamp') != list(state['source_stamp'])
            or saved.get('dimensions') != [state['height'], state['width']]
            or entry.get('source') != str(state['source']) or entry.get('plane') != state['plane']):
        raise ValueError('Saved results do not match this image/channel or the image changed. Detect and save again.')
    if saved.get('inference') != entry.get('inference') or saved.get('params') != entry.get('params'):
        raise ValueError('Saved parameters and candidate snapshot do not match.')
    records, floor = saved['records'], saved['floor']
    if not isinstance(records, list) or not isinstance(floor, (int, float)) or not math.isfinite(floor) or not 0 < floor <= 1:
        raise ValueError('Invalid saved candidates.')
    for row in records:
        if not isinstance(row, list) or len(row) != 8 or any(not isinstance(value,(int,float)) or not math.isfinite(value) for value in row[:6]) or any(value is not None and (not isinstance(value,(int,float)) or not math.isfinite(value)) for value in row[6:]):
            raise ValueError('Invalid saved candidate measurements.')
    indices = filter_indices(records, saved['params'], floor)
    if indices != saved.get('indices') or len(indices) != entry.get('count') or file_hash(prediction) != saved.get('prediction_hash'):
        raise ValueError('Saved BBoxes and parameters are inconsistent. Detect and save again.')
    return {'records': records, 'indices': indices, 'floor': floor, 'params': saved['params'],
            'inference': saved['inference'], 'method': entry.get('method', 'custom')}
