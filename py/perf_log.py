"""Lightweight, env-gated performance timing for Mason Jar pipeline steps.

Enable by setting the environment variable ``MASONJAR_PERF=1`` (also accepts
true/yes/on). All output is written to stdout as ``LOG: perf <label> <ms>``
lines, which the app already captures into ``~/.masonjar/masonjar.log`` and the
in-app Log window. When disabled the helpers are near-zero cost (no timing, no
output), so they are safe to leave in production code.

Usage:
    import perf_log

    # Total wall time of a whole step (one line at the entry point):
    perf_log.perf_start_total("count")

    # Time a specific block:
    with perf_log.perf_section("count.load_masks"):
        ...

    # Decorate a function:
    @perf_log.perf_timed("count.per_slice")
    def process(...):
        ...

Review: set MASONJAR_PERF=1, run the step, then filter ``LOG: perf`` in
masonjar.log or the in-app Log window.
"""

from __future__ import annotations

import atexit
import os
import time
import tracemalloc
from contextlib import contextmanager

PERF_ENABLED = os.environ.get("MASONJAR_PERF", "").strip().lower() in (
    "1",
    "true",
    "yes",
    "on",
)

if PERF_ENABLED:
    # Python allocation peak is portable and has near-zero overhead when the
    # existing performance switch is off. RSS is added when psutil is present.
    tracemalloc.start()


def perf_enabled() -> bool:
    """Return True when perf logging is enabled via MASONJAR_PERF."""
    return PERF_ENABLED


def perf_log(label: str, ms: float) -> None:
    """Emit a single ``LOG: perf <label> <ms>`` line (only when enabled)."""
    if PERF_ENABLED:
        print(f"LOG: perf {label} {ms:.1f}ms", flush=True)


def perf_memory(label: str) -> None:
    """Emit traced Python peak and optional process RSS in MiB."""
    if not PERF_ENABLED:
        return
    _current, peak = tracemalloc.get_traced_memory()
    rss = None
    try:
        import psutil  # type: ignore

        rss = psutil.Process().memory_info().rss / 1048576.0
    except Exception:
        pass
    suffix = f" rss={rss:.1f}MiB" if rss is not None else ""
    print(f"LOG: memory {label} python_peak={peak / 1048576.0:.1f}MiB{suffix}", flush=True)


@contextmanager
def perf_section(label: str):
    """Time a code block; emits the duration on exit when enabled.

    Near-zero cost when disabled (no perf_counter calls, no output).
    """
    if not PERF_ENABLED:
        yield
        return
    _t = time.perf_counter()
    try:
        yield
    finally:
        perf_log(label, (time.perf_counter() - _t) * 1000.0)


def perf_timed(label: str):
    """Decorator variant of :func:`perf_section`."""

    def _decorator(fn):
        def _wrapped(*args, **kwargs):
            if not PERF_ENABLED:
                return fn(*args, **kwargs)
            _t = time.perf_counter()
            try:
                return fn(*args, **kwargs)
            finally:
                perf_log(label, (time.perf_counter() - _t) * 1000.0)

        return _wrapped

    return _decorator


def perf_start_total(label: str) -> None:
    """Record process start and emit total wall time on interpreter exit.

    A single call at a step's entry point instruments the whole step without
    wrapping or re-indenting its body. Each pipeline step runs as its own
    Python process, so the atexit handler fires exactly once per step.
    """
    if not PERF_ENABLED:
        return
    _t0 = time.perf_counter()
    atexit.register(
        lambda: print(
            f"LOG: perf {label}.total "
            f"{(time.perf_counter() - _t0) * 1000.0:.1f}ms",
            flush=True,
        )
    )
