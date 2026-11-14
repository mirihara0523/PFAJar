"""Bound native CPU parallelism without spawning additional image workers."""
from __future__ import annotations

import os
import sys


def available_cpus():
    try:
        return max(1, len(os.sched_getaffinity(0)))
    except (AttributeError, OSError):
        return max(1, os.cpu_count() or 1)


def cpu_threads():
    cpus = available_cpus()
    raw = os.environ.get("MASONJAR_CPU_THREADS", "auto").strip().lower()
    if raw not in ("", "auto"):
        try:
            return max(1, min(cpus, int(raw)))
        except ValueError:
            pass
    # Leave room for the desktop and avoid large native thread pools on NAS jobs.
    return max(1, min(8, cpus - 1))


def configure_cpu(log=True):
    threads = cpu_threads()
    # Explicit library-specific environment settings remain authoritative.
    for key in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS",
                "NUMEXPR_NUM_THREADS", "TORCH_NUM_THREADS"):
        os.environ.setdefault(key, str(threads))
    cv2 = sys.modules.get("cv2")
    if cv2 is None:
        try:
            import cv2
        except ImportError:
            cv2 = None
    if cv2 is not None:
        cv2.setNumThreads(threads)
    torch = sys.modules.get("torch")
    if torch is not None:
        try:
            torch_threads = max(1, int(os.environ["TORCH_NUM_THREADS"]))
        except ValueError:
            torch_threads = threads
        torch.set_num_threads(torch_threads)
        try:
            torch.set_num_interop_threads(1)
        except RuntimeError:
            # PyTorch only permits setting this before inter-op work begins.
            pass
    if log:
        print(f"LOG: cpu_runtime available={available_cpus()} threads={threads}", flush=True)
    return threads
