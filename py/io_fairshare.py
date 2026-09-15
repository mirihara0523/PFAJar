"""Machine-wide adaptive NAS/SMB I/O fair-share for Mason Jar pipeline jobs."""

from __future__ import annotations

import atexit
import json
import os
import socket
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable

_SMALL_FILE_BYTES = int(os.environ.get("MASONJAR_IO_SMALL_FILE_BYTES", str(256 * 1024)))
_CHUNK_BYTES = int(os.environ.get("MASONJAR_IO_CHUNK_BYTES", str(2 * 1024 * 1024)))
_STALE_SECONDS = float(os.environ.get("MASONJAR_IO_STALE_SECONDS", "30"))
_HEARTBEAT_SECONDS = 5.0
_BYTES_WINDOW_SECONDS = 60.0

_state_lock = threading.RLock()
_byte_samples: list[tuple[float, int]] = []
_activated = False
_job_id = ""
_job_label = ""
_coordinator_dir = ""
_heartbeat_stop: threading.Event | None = None
_heartbeat_thread: threading.Thread | None = None
_orig: dict[str, Any] = {}


def _env_enabled() -> bool:
    return os.environ.get("MASONJAR_IO_FAIRSHARE", "1") not in ("0", "false", "False")


def default_coordinator_dir() -> Path:
    override = os.environ.get("MASONJAR_IO_FAIRSHARE_DIR", "").strip()
    if override:
        return Path(override)
    if sys.platform == "win32":
        program_data = os.environ.get("ProgramData", r"C:\ProgramData")
        return Path(program_data) / "MasonJar" / "io-fairshare"
    if sys.platform == "darwin":
        return Path("/Library/Application Support/MasonJar/io-fairshare")
    return Path("/var/run/masonjar-io-fairshare")


def _registry_dir() -> Path:
    return Path(_coordinator_dir) / "registry"


def _load_shared_config() -> dict[str, Any]:
    cfg_path = Path(_coordinator_dir) / "config.json"
    defaults = {
        "enabled": True,
        "link_mbps": "auto",
        "headroom": float(os.environ.get("MASONJAR_IO_HEADROOM", "0.85")),
        "min_mbps_per_job": float(os.environ.get("MASONJAR_IO_MIN_MBPS", "25")),
        "max_mbps_per_job": "auto",
        "small_file_bytes": _SMALL_FILE_BYTES,
        "stale_seconds": _STALE_SECONDS,
        "nas_path_prefixes": [],
    }
    try:
        if cfg_path.is_file():
            with open(cfg_path, encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                defaults.update(data)
    except OSError:
        pass
    return defaults


def _resolve_link_mbps(cfg: dict[str, Any]) -> float:
    env_link = os.environ.get("MASONJAR_IO_LINK_MBPS", "").strip()
    if env_link:
        try:
            return max(1.0, float(env_link))
        except ValueError:
            pass
    link = cfg.get("link_mbps", "auto")
    if link != "auto":
        try:
            return max(1.0, float(link))
        except (TypeError, ValueError):
            pass
    return 1000.0


def _resolve_max_mbps(cfg: dict[str, Any], link_mbps: float) -> float:
    env_max = os.environ.get("MASONJAR_IO_MAX_MBPS", "").strip()
    if env_max:
        try:
            return max(1.0, float(env_max))
        except ValueError:
            pass
    headroom = float(cfg.get("headroom", 0.85))
    min_mbps = float(cfg.get("min_mbps_per_job", 25))
    max_val = cfg.get("max_mbps_per_job", "auto")
    if max_val == "auto":
        return max(min_mbps, link_mbps * headroom)
    try:
        return max(min_mbps, float(max_val))
    except (TypeError, ValueError):
        return max(min_mbps, link_mbps * headroom)


def _list_active_jobs() -> list[dict[str, Any]]:
    reg = _registry_dir()
    if not reg.is_dir():
        return []
    now = time.time()
    active: list[dict[str, Any]] = []
    for path in reg.glob("*.json"):
        try:
            with open(path, encoding="utf-8") as f:
                entry = json.load(f)
            hb = entry.get("last_heartbeat")
            if not hb:
                path.unlink(missing_ok=True)
                continue
            age = now - _parse_iso(hb)
            if age > _STALE_SECONDS:
                path.unlink(missing_ok=True)
                continue
            active.append(entry)
        except (OSError, json.JSONDecodeError, TypeError):
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
    return active


def _parse_iso(value: str) -> float:
    try:
        # Python 3.10: fromisoformat handles most ISO strings
        from datetime import datetime

        if value.endswith("Z"):
            value = value[:-1] + "+00:00"
        return datetime.fromisoformat(value).timestamp()
    except Exception:
        return 0.0


def compute_limit_mbps() -> float:
    cfg = _load_shared_config()
    link_mbps = _resolve_link_mbps(cfg)
    headroom = float(cfg.get("headroom", 0.85))
    min_mbps = float(cfg.get("min_mbps_per_job", 25))
    max_mbps = _resolve_max_mbps(cfg, link_mbps)
    active = max(1, len(_list_active_jobs()))
    budget = link_mbps * headroom
    raw = budget / active
    return min(max_mbps, max(min_mbps, raw))


def _throttled_mbps_1m() -> float:
    """Rolling 1-minute average Mbps for throttled NAS bytes."""
    global _byte_samples
    now = time.monotonic()
    total = _BUCKET.bytes_total()
    with _state_lock:
        _byte_samples.append((now, total))
        cutoff = now - _BYTES_WINDOW_SECONDS
        _byte_samples = [(t, b) for t, b in _byte_samples if t >= cutoff]
        if len(_byte_samples) < 2:
            return 0.0
        oldest = _byte_samples[0]
        newest = _byte_samples[-1]
        dt = newest[0] - oldest[0]
        if dt <= 0:
            return 0.0
        delta_bytes = newest[1] - oldest[1]
        return max(0.0, (delta_bytes * 8.0) / dt / 1_000_000.0)


def _write_registry() -> None:
    if not _job_id:
        return
    reg = _registry_dir()
    reg.mkdir(parents=True, exist_ok=True)
    app_instance_id = os.environ.get("MASONJAR_IO_APP_INSTANCE_ID", "").strip()
    existing_path = reg / f"{_job_id}.json"
    started_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    try:
        if existing_path.is_file():
            with open(existing_path, encoding="utf-8") as f:
                prev = json.load(f)
            if isinstance(prev, dict) and prev.get("started_at"):
                started_at = str(prev["started_at"])
    except (OSError, json.JSONDecodeError, TypeError):
        pass
    entry: dict[str, Any] = {
        "job_id": _job_id,
        "pid": os.getpid(),
        "user": os.environ.get("USERNAME") or os.environ.get("USER") or "unknown",
        "hostname": socket.gethostname(),
        "label": _job_label,
        "started_at": started_at,
        "last_heartbeat": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "throttled_bytes_total": _BUCKET.bytes_total(),
        "throttled_mbps_1m": round(_throttled_mbps_1m(), 2),
    }
    if app_instance_id:
        entry["app_instance_id"] = app_instance_id
    path = reg / f"{_job_id}.json"
    tmp = path.with_suffix(f".{os.getpid()}.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(entry, f, indent=2)
    os.replace(tmp, path)


def _remove_registry() -> None:
    if not _job_id:
        return
    path = _registry_dir() / f"{_job_id}.json"
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


def _heartbeat_loop(stop: threading.Event) -> None:
    while not stop.wait(_HEARTBEAT_SECONDS):
        try:
            _write_registry()
        except OSError:
            pass


class TokenBucket:
    def __init__(self) -> None:
        self._tokens = 0.0
        self._last = time.monotonic()
        self._lock = threading.Lock()
        self._bytes_total = 0

    def bytes_total(self) -> int:
        with self._lock:
            return self._bytes_total

    def _refill(self) -> None:
        now = time.monotonic()
        elapsed = max(0.0, now - self._last)
        self._last = now
        limit_mbps = compute_limit_mbps()
        bytes_per_sec = limit_mbps * 125_000.0  # megabits -> bytes/s
        burst = bytes_per_sec * 2.0
        self._tokens = min(burst, self._tokens + elapsed * bytes_per_sec)

    def consume(self, nbytes: int) -> None:
        if nbytes <= 0:
            return
        while True:
            with self._lock:
                self._refill()
                if self._tokens >= nbytes:
                    self._tokens -= nbytes
                    self._bytes_total += nbytes
                    return
                need = nbytes - self._tokens
                limit_mbps = compute_limit_mbps()
                bytes_per_sec = max(1.0, limit_mbps * 125_000.0)
                wait_s = need / bytes_per_sec
            time.sleep(min(0.25, max(0.001, wait_s)))


_BUCKET = TokenBucket()


def _nas_prefixes() -> list[str]:
    cfg = _load_shared_config()
    prefixes = list(cfg.get("nas_path_prefixes") or [])
    env = os.environ.get("MASONJAR_IO_NAS_PREFIXES", "").strip()
    if env:
        prefixes.extend(p.strip() for p in env.split(";") if p.strip())
    return [os.fspath(p) for p in prefixes]


def _should_throttle(path: str | os.PathLike[str]) -> bool:
    text = os.path.normcase(os.fspath(path))
    if sys.platform == "win32":
        if text.startswith("\\\\"):
            return True
        for pref in _nas_prefixes():
            if text.startswith(os.path.normcase(pref)):
                return True
        return False
    if text.startswith("/volumes/"):
        return True
    for pref in _nas_prefixes():
        if text.startswith(os.path.normcase(pref)):
            return True
    return False


def _file_size(path: str | os.PathLike[str]) -> int | None:
    try:
        return os.path.getsize(os.fspath(path))
    except OSError:
        return None


def _path_read_bytes(path: str | os.PathLike[str]) -> bytes:
    p = Path(os.fspath(path))
    if "Path.read_bytes" in _orig:
        return _orig["Path.read_bytes"](p)
    return p.read_bytes()


def _path_write_bytes(path: str | os.PathLike[str], data: bytes) -> None:
    p = Path(os.fspath(path))
    if "Path.write_bytes" in _orig:
        _orig["Path.write_bytes"](p, data)
    else:
        p.write_bytes(data)


def throttled_read_bytes(path: str | os.PathLike[str]) -> bytes:
    p = os.fspath(path)
    if not _activated or not _should_throttle(p):
        return _path_read_bytes(p)
    size = _file_size(p)
    small = int(_load_shared_config().get("small_file_bytes", _SMALL_FILE_BYTES))
    if size is not None and size <= small:
        return _path_read_bytes(p)
    chunks: list[bytes] = []
    with open(p, "rb") as f:
        while True:
            block = f.read(_CHUNK_BYTES)
            if not block:
                break
            _BUCKET.consume(len(block))
            chunks.append(block)
    return b"".join(chunks)


def throttled_write_bytes(path: str | os.PathLike[str], data: bytes) -> None:
    p = os.fspath(path)
    if not _activated or not _should_throttle(p):
        _path_write_bytes(p, data)
        return
    small = int(_load_shared_config().get("small_file_bytes", _SMALL_FILE_BYTES))
    if len(data) <= small:
        _path_write_bytes(p, data)
        return
    Path(p).parent.mkdir(parents=True, exist_ok=True)
    offset = 0
    with open(p, "wb") as f:
        while offset < len(data):
            block = data[offset : offset + _CHUNK_BYTES]
            _BUCKET.consume(len(block))
            f.write(block)
            offset += len(block)


# Cap for header/metadata probes (TiffFile opens) so we rate-limit without
# reading full multi-GB planes through the token bucket.
_ACCOUNT_NAS_OPEN_CAP_BYTES = int(
    os.environ.get("MASONJAR_IO_ACCOUNT_OPEN_CAP", str(1024 * 1024))
)


def account_nas_open(path: str | os.PathLike[str]) -> None:
    """Charge the token bucket for a NAS open that bypasses patched readers.

    Used by index_metadata (tifffile.TiffFile header probes). Consumes
    ``min(file_size, 1 MiB)`` when fairshare is active and the path is NAS.
    No-op when inactive, local, or for small files under the bypass threshold.
    """
    if not _activated:
        return
    p = os.fspath(path)
    if not _should_throttle(p):
        return
    size = _file_size(p)
    if size is None:
        return
    small = int(_load_shared_config().get("small_file_bytes", _SMALL_FILE_BYTES))
    if size <= small:
        return
    _BUCKET.consume(min(size, _ACCOUNT_NAS_OPEN_CAP_BYTES))


def _wrap_cv2_imread(orig: Callable[..., Any], path: str, *args: Any, **kwargs: Any) -> Any:
    import cv2
    import numpy as np

    p = os.fspath(path)
    if not _activated or not _should_throttle(p):
        return orig(path, *args, **kwargs)
    size = _file_size(p)
    small = int(_load_shared_config().get("small_file_bytes", _SMALL_FILE_BYTES))
    if size is not None and size <= small:
        return orig(path, *args, **kwargs)
    raw = throttled_read_bytes(p)
    arr = np.frombuffer(raw, dtype=np.uint8)
    flag = args[0] if args else kwargs.get("flags", cv2.IMREAD_UNCHANGED)
    return cv2.imdecode(arr, flag)


def _wrap_cv2_imwrite(orig: Callable[..., Any], path: str, img: Any, *args: Any, **kwargs: Any) -> Any:
    import cv2

    p = os.fspath(path)
    if not _activated or not _should_throttle(p):
        return orig(path, img, *args, **kwargs)
    ext = os.path.splitext(p)[1] or ".png"
    ok, buf = cv2.imencode(ext, img)
    if not ok:
        return orig(path, img, *args, **kwargs)
    throttled_write_bytes(p, buf.tobytes())
    return True


def _is_fspath_like(path: Any) -> bool:
    try:
        os.fspath(path)
    except TypeError:
        return False
    return True


def _wrap_tiff_imread(orig: Callable[..., Any], path: str, *args: Any, **kwargs: Any) -> Any:
    import io

    if not _is_fspath_like(path):
        return orig(path, *args, **kwargs)
    p = os.fspath(path)
    if not _activated or not _should_throttle(p):
        return orig(path, *args, **kwargs)
    size = _file_size(p)
    small = int(_load_shared_config().get("small_file_bytes", _SMALL_FILE_BYTES))
    if size is not None and size <= small:
        return orig(path, *args, **kwargs)
    raw = throttled_read_bytes(p)
    return orig(io.BytesIO(raw), *args, **kwargs)


def _wrap_tiff_imwrite(orig: Callable[..., Any], path: str, data: Any, *args: Any, **kwargs: Any) -> Any:
    import io

    if not _is_fspath_like(path):
        return orig(path, data, *args, **kwargs)
    p = os.fspath(path)
    if not _activated or not _should_throttle(p):
        return orig(path, data, *args, **kwargs)
    buf = io.BytesIO()
    orig(buf, data, *args, **kwargs)
    throttled_write_bytes(p, buf.getvalue())
    return None


def _wrap_path_read_bytes(orig: Callable[..., bytes], self: Path) -> bytes:
    if not _activated or not _should_throttle(self):
        return orig(self)
    return throttled_read_bytes(self)


def _wrap_path_write_bytes(orig: Callable[..., int], self: Path, data: bytes) -> int:
    if not _activated or not _should_throttle(self):
        return orig(self, data)
    throttled_write_bytes(self, data)
    return len(data)


def _install_patches() -> None:
    global _orig
    import cv2
    import tifffile
    from pathlib import Path

    if "cv2.imread" not in _orig:
        _orig["cv2.imread"] = cv2.imread
        cv2.imread = lambda path, *a, **k: _wrap_cv2_imread(_orig["cv2.imread"], path, *a, **k)  # type: ignore[assignment]
    if "cv2.imwrite" not in _orig:
        _orig["cv2.imwrite"] = cv2.imwrite
        cv2.imwrite = lambda path, img, *a, **k: _wrap_cv2_imwrite(_orig["cv2.imwrite"], path, img, *a, **k)  # type: ignore[assignment]
    if "tifffile.imread" not in _orig:
        _orig["tifffile.imread"] = tifffile.imread
        tifffile.imread = lambda path, *a, **k: _wrap_tiff_imread(_orig["tifffile.imread"], path, *a, **k)  # type: ignore[assignment]
    if "tifffile.imwrite" not in _orig:
        _orig["tifffile.imwrite"] = tifffile.imwrite
        tifffile.imwrite = lambda path, data, *a, **k: _wrap_tiff_imwrite(_orig["tifffile.imwrite"], path, data, *a, **k)  # type: ignore[assignment]
    if "Path.read_bytes" not in _orig:
        _orig["Path.read_bytes"] = Path.read_bytes
        Path.read_bytes = lambda self: _wrap_path_read_bytes(_orig["Path.read_bytes"], self)  # type: ignore[assignment]
    if "Path.write_bytes" not in _orig:
        _orig["Path.write_bytes"] = Path.write_bytes
        Path.write_bytes = lambda self, data: _wrap_path_write_bytes(_orig["Path.write_bytes"], self, data)  # type: ignore[assignment]


def deactivate() -> None:
    """Stop heartbeat and remove this process's registry entry.

    The long-lived masonjar_worker calls this after each runpy job so the next
    script can activate() with a fresh MASONJAR_IO_JOB_ID. Also registered with
    atexit for one-shot PythonShell processes.

    When ``MASONJAR_IO_KEEP_REGISTRY=1`` (Node owns the registry entry for a
    parent session such as project_index), stop the Python heartbeat but leave
    the registry file for Node's ``endNodeJobTracking`` to clear.
    """
    global _activated, _heartbeat_stop, _heartbeat_thread
    with _state_lock:
        if not _activated:
            return
        _activated = False
        if _heartbeat_stop is not None:
            _heartbeat_stop.set()
        if _heartbeat_thread is not None:
            _heartbeat_thread.join(timeout=1.0)
        _heartbeat_stop = None
        _heartbeat_thread = None
        keep = os.environ.get("MASONJAR_IO_KEEP_REGISTRY", "").strip() in (
            "1",
            "true",
            "True",
        )
        if not keep:
            _remove_registry()


def activate() -> bool:
    """Enable fair-share I/O patches and register this job.

    Reads MASONJAR_IO_JOB_ID / MASONJAR_IO_JOB_LABEL from the environment.
    Idempotent while already activated (early return) — worker must
    deactivate() between jobs so the next script can rebind id/label.
    """
    global _activated, _job_id, _job_label, _coordinator_dir
    global _heartbeat_stop, _heartbeat_thread
    if not _env_enabled():
        return False
    with _state_lock:
        if _activated:
            return True
        _coordinator_dir = os.environ.get("MASONJAR_IO_FAIRSHARE_DIR", "").strip()
        if not _coordinator_dir:
            _coordinator_dir = str(default_coordinator_dir())
        _job_id = os.environ.get("MASONJAR_IO_JOB_ID", "").strip()
        _job_label = os.environ.get("MASONJAR_IO_JOB_LABEL", "pipeline").strip() or "pipeline"
        if not _job_id:
            _job_id = f"py-{os.getpid()}-{int(time.time())}"
        try:
            _registry_dir().mkdir(parents=True, exist_ok=True)
        except OSError:
            return False
        _install_patches()
        _write_registry()
        _heartbeat_stop = threading.Event()
        _heartbeat_thread = threading.Thread(
            target=_heartbeat_loop,
            args=(_heartbeat_stop,),
            daemon=True,
            name="io_fairshare_heartbeat",
        )
        _heartbeat_thread.start()
        _activated = True
        active = len(_list_active_jobs())
        limit = compute_limit_mbps()
        print(
            f"LOG: io_fairshare active jobs={active} limit={limit:.1f}Mbps label={_job_label}",
            flush=True,
        )
        return True


atexit.register(deactivate)
