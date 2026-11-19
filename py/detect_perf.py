"""Stage timing without allocation tracing during detection."""
import os
import time
from contextlib import contextmanager


@contextmanager
def section(stage, label=""):
    if os.environ.get("MASONJAR_PERF", "").strip().lower() not in ("1", "true", "yes", "on"):
        yield
        return
    started = time.perf_counter()
    try:
        yield
    finally:
        print(f"LOG: perf detect.{stage} {(time.perf_counter() - started) * 1000:.1f}ms"
              + (f" slice={label}" if label else ""), flush=True)


def call(stage, label, function, *args, **kwargs):
    with section(stage, label):
        return function(*args, **kwargs)
