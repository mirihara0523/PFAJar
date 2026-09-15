"""czi_probe emits a liveness heartbeat while a single large CZI is read.

Regression for the "probe looks hung" report: a multi-GB mosaic can take
minutes to open/scan over a busy NAS, during which ``czi_probe`` previously
printed ``Probing X.czi`` and then nothing. The heartbeat must emit periodic
``LOG:`` lines (which the main process maps to the wizard probe log) without
doing any extra I/O.
"""

from __future__ import annotations

import io
import sys
import time
from contextlib import redirect_stdout
from pathlib import Path

PY_DIR = Path(__file__).resolve().parents[2] / "py"
sys.path.insert(0, str(PY_DIR))

import czi_probe  # noqa: E402


def test_heartbeat_emits_log_lines_during_slow_probe(monkeypatch) -> None:
    def _slow_probe(path: Path) -> dict:
        time.sleep(0.65)  # ~3 beats at a 0.2s interval
        return {"basename": path.name, "ok": True}

    monkeypatch.setattr(czi_probe, "probe_file", _slow_probe)
    monkeypatch.setattr(czi_probe, "PROBE_HEARTBEAT_INTERVAL_S", 0.2)

    buf = io.StringIO()
    with redirect_stdout(buf):
        result = czi_probe._probe_file_with_heartbeat(Path("M457(1).czi"))

    out = buf.getvalue()
    beats = [ln for ln in out.splitlines() if ln.startswith("LOG:") and "still probing" in ln]
    assert result == {"basename": "M457(1).czi", "ok": True}
    assert len(beats) >= 2, f"expected >=2 heartbeat lines, got: {out!r}"
    assert "M457(1).czi" in beats[0]


def test_heartbeat_stops_promptly_for_fast_probe(monkeypatch) -> None:
    def _fast_probe(path: Path) -> dict:
        return {"basename": path.name, "ok": True}

    monkeypatch.setattr(czi_probe, "probe_file", _fast_probe)
    monkeypatch.setattr(czi_probe, "PROBE_HEARTBEAT_INTERVAL_S", 0.2)

    buf = io.StringIO()
    with redirect_stdout(buf):
        czi_probe._probe_file_with_heartbeat(Path("M457(2).czi"))

    # A sub-interval probe should not emit any heartbeat noise.
    beats = [ln for ln in buf.getvalue().splitlines() if "still probing" in ln]
    assert beats == [], f"unexpected heartbeats for fast probe: {beats}"
