#!/usr/bin/env python3
"""Long-lived in-process runner for Electron py/ scripts.

Reads newline-delimited JSON on stdin; writes newline-delimited JSON on stdout.
Runs allowlisted scripts via runpy in this process (no per-job child process).

Protocol:
  <- {"cmd":"run","id":"...","script":"max.py","args":[...],"env":{...}}
  -> {"id":"...","type":"line","data":"..."}
  -> {"id":"...","type":"stderr","data":"..."}
  -> {"id":"...","type":"done","code":0}
  <- {"cmd":"cancel","id":"..."}
  <- {"cmd":"shutdown"}
  -> {"type":"ready"}
"""

from __future__ import annotations

import json
import os
import runpy
import sys
import threading
import time
import traceback
from typing import Any, Dict, Optional, TextIO

# Force single-threaded torch/data loaders when scripts import them later.
os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("MKL_NUM_THREADS", "1")
os.environ.setdefault("TORCH_NUM_THREADS", "1")

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_cancel = threading.Event()
_current_id: Optional[str] = None
_lock = threading.Lock()


def _emit(obj: Dict[str, Any]) -> None:
    sys.__stdout__.write(json.dumps(obj, ensure_ascii=False) + "\n")
    sys.__stdout__.flush()


class _LineWriter:
    def __init__(self, job_id: str, stream_type: str) -> None:
        self.job_id = job_id
        self.stream_type = stream_type
        self._buf = ""

    def write(self, s: str) -> int:
        if not isinstance(s, str):
            s = str(s)
        self._buf += s
        while "\n" in self._buf:
            line, self._buf = self._buf.split("\n", 1)
            _emit({"id": self.job_id, "type": self.stream_type, "data": line})
        return len(s)

    def flush(self) -> None:
        if self._buf:
            _emit({"id": self.job_id, "type": self.stream_type, "data": self._buf})
            self._buf = ""

    def isatty(self) -> bool:
        return False


def _apply_env(env: Optional[Dict[str, str]]) -> Dict[str, Optional[str]]:
    old: Dict[str, Optional[str]] = {}
    if not env:
        return old
    for key, val in env.items():
        if not isinstance(key, str):
            continue
        old[key] = os.environ.get(key)
        if val is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = str(val)
    return old


def _restore_env(old: Dict[str, Optional[str]]) -> None:
    for key, val in old.items():
        if val is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = val


def _run_script(job_id: str, script: str, args: list, env: Optional[Dict[str, str]]) -> int:
    global _current_id
    script_name = os.path.basename(script)
    if ".." in script_name or script_name != script.replace("\\", "/").split("/")[-1]:
        _emit({"id": job_id, "type": "error", "message": "invalid script name", "code": 2})
        return 2
    script_path = os.path.join(SCRIPT_DIR, script_name)
    if not os.path.isfile(script_path):
        _emit({"id": job_id, "type": "error", "message": f"missing script {script_name}", "code": 2})
        return 2

    old_env = _apply_env(env)
    old_argv = sys.argv[:]
    old_stdout = sys.stdout
    old_stderr = sys.stderr
    sys.argv = [script_path] + [str(a) for a in (args or [])]
    sys.stdout = _LineWriter(job_id, "line")  # type: ignore[assignment]
    sys.stderr = _LineWriter(job_id, "stderr")  # type: ignore[assignment]
    with _lock:
        _current_id = job_id
        _cancel.clear()
    code = 0
    # Per-job total-time timing (env-gated). Done here rather than via atexit in
    # each script, because the worker process is long-lived: atexit would not
    # fire per job. os.environ already carries this job's env (_apply_env above).
    _perf_on = os.environ.get("MASONJAR_PERF", "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )
    _perf_t0 = time.perf_counter() if _perf_on else 0.0
    try:
        try:
            import torch  # type: ignore

            try:
                torch.set_num_threads(1)
            except Exception:
                pass
        except Exception:
            pass
        runpy.run_path(script_path, run_name="__main__")
    except SystemExit as exc:
        c = exc.code
        if c is None:
            code = 0
        elif isinstance(c, int):
            code = c
        else:
            code = 1
    except BaseException:
        traceback.print_exc()
        code = 1
    finally:
        if _perf_on:
            # Emit while the job's line-writer stdout is still installed so the
            # line is captured to masonjar.log like other LOG lines.
            try:
                print(
                    f"LOG: perf {script_name}.total "
                    f"{(time.perf_counter() - _perf_t0) * 1000.0:.1f}ms",
                    flush=True,
                )
            except Exception:
                pass
        try:
            sys.stdout.flush()
            sys.stderr.flush()
        except Exception:
            pass
        sys.argv = old_argv
        sys.stdout = old_stdout
        sys.stderr = old_stderr
        _restore_env(old_env)
        with _lock:
            _current_id = None
            _cancel.clear()
        # Worker-hosted scripts must finish fair-share I/O before __main__ returns.
        # Tear down registry/heartbeat so the next job can activate() with a fresh id.
        try:
            import io_fairshare

            io_fairshare.deactivate()
        except Exception:
            pass
    return code


def _handle(msg: Dict[str, Any]) -> None:
    cmd = msg.get("cmd")
    if cmd == "ping":
        _emit({"type": "pong"})
        return
    if cmd == "shutdown":
        _emit({"type": "bye"})
        raise SystemExit(0)
    if cmd == "cancel":
        # Cooperative only; in-process scripts are not preempted mid-opcode.
        _cancel.set()
        return
    if cmd == "run":
        job_id = str(msg.get("id") or "")
        script = str(msg.get("script") or "")
        args = msg.get("args") or []
        env = msg.get("env") if isinstance(msg.get("env"), dict) else None
        if not job_id or not script:
            _emit({"id": job_id, "type": "error", "message": "id and script required", "code": 2})
            return
        if not isinstance(args, list):
            args = []
        code = _run_script(job_id, script, args, env)
        _emit({"id": job_id, "type": "done", "code": int(code)})
        return
    _emit({"type": "error", "message": f"unknown cmd {cmd}"})


def main() -> None:
    _emit({"type": "ready"})
    for raw in sys.stdin:
        line = raw.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            _emit({"type": "error", "message": "invalid json"})
            continue
        if not isinstance(msg, dict):
            continue
        try:
            _handle(msg)
        except SystemExit:
            raise
        except Exception as exc:
            _emit({"type": "error", "message": str(exc)})


if __name__ == "__main__":
    main()
