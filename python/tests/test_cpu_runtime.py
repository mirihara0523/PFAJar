"""CPU budget and persistent-worker configuration regression checks."""
import importlib.util
from pathlib import Path
from types import SimpleNamespace
import sys

spec = importlib.util.spec_from_file_location(
    "cpu_runtime", Path(__file__).parents[2] / "py" / "cpu_runtime.py"
)
cpu = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cpu)


def test_auto_budget(monkeypatch):
    monkeypatch.delenv("MASONJAR_CPU_THREADS", raising=False)
    for available, expected in ((1, 1), (4, 3), (32, 8)):
        monkeypatch.setattr(cpu, "available_cpus", lambda: available)
        assert cpu.cpu_threads() == expected


def test_override_clamped_and_invalid_falls_back(monkeypatch):
    monkeypatch.setattr(cpu, "available_cpus", lambda: 4)
    for raw, expected in (("1", 1), ("99", 4), ("0", 1), ("bad", 3)):
        monkeypatch.setenv("MASONJAR_CPU_THREADS", raw)
        assert cpu.cpu_threads() == expected


def test_loaded_libraries_and_explicit_environment(monkeypatch):
    monkeypatch.setattr(cpu, "available_cpus", lambda: 8)
    monkeypatch.setenv("MASONJAR_CPU_THREADS", "4")
    monkeypatch.setenv("TORCH_NUM_THREADS", "2")
    monkeypatch.setenv("OMP_NUM_THREADS", "3")
    calls = []
    def interop(_):
        raise RuntimeError("already initialized")
    monkeypatch.setitem(sys.modules, "torch", SimpleNamespace(
        set_num_threads=lambda n: calls.append(("torch", n)),
        set_num_interop_threads=interop))
    monkeypatch.setitem(sys.modules, "cv2", SimpleNamespace(
        setNumThreads=lambda n: calls.append(("cv2", n))))
    assert cpu.configure_cpu(log=False) == 4
    assert calls == [("cv2", 4), ("torch", 2)]
    assert cpu.os.environ["OMP_NUM_THREADS"] == "3"
