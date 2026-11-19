"""Local SAHI candidate cache; intensity cutoff is applied after retrieval."""
import hashlib
import json
import os
import pickle
import tempfile
from pathlib import Path


def file_identity(path):
    path = Path(path).resolve()
    stat = path.stat()
    return [str(path), stat.st_size, stat.st_mtime_ns]


def cache_key(source, model, settings):
    payload = {"schema": 1, "source": file_identity(source),
               "model": file_identity(model), "settings": settings}
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def cached_prediction(directory, key, compute):
    directory = Path(directory)
    target = directory / (key + ".pkl")
    try:
        with target.open("rb") as stream:
            return pickle.load(stream), True
    except (OSError, EOFError, pickle.UnpicklingError, AttributeError, ImportError):
        pass
    result = compute()
    # A cache write failure must not turn a successful detection into a failure.
    temporary = None
    try:
        directory.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=directory, delete=False) as stream:
            temporary = stream.name
            pickle.dump(result, stream, protocol=pickle.HIGHEST_PROTOCOL)
        os.replace(temporary, target)
    except (OSError, pickle.PickleError, TypeError):
        pass
    finally:
        if temporary and os.path.exists(temporary):
            os.unlink(temporary)
    return result, False
