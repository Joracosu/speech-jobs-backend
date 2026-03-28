"""Tests for the worker CLI entrypoint."""

from __future__ import annotations

import importlib
import sys
from types import ModuleType, SimpleNamespace

import pytest

from app.worker.runtime_checks import ComponentRuntimeStatus, WorkerRuntimeStatus


def _runtime_report(*, requested_device: str, ready: bool) -> WorkerRuntimeStatus:
    """Build a minimal worker runtime report for CLI tests."""
    component_status = ComponentRuntimeStatus(
        component="asr",
        requested_device=requested_device,
        resolved_device=requested_device,
        ready=ready,
        issues=(),
    )
    diarization_status = ComponentRuntimeStatus(
        component="diarization",
        requested_device=requested_device,
        resolved_device=requested_device,
        ready=ready,
        issues=(),
    )
    return WorkerRuntimeStatus(
        requested_device=requested_device,
        ready=ready,
        asr=component_status,
        diarization=diarization_status,
    )


def _load_worker_main_module() -> ModuleType:
    """Import and reload the worker main module for isolation-sensitive tests."""
    module = importlib.import_module("app.worker.main")
    return importlib.reload(module)


def _make_module(name: str, **attrs: object) -> ModuleType:
    """Create a lightweight module object with the provided attributes."""
    module = ModuleType(name)
    for key, value in attrs.items():
        setattr(module, key, value)
    return module


def test_main_preflight_uses_device_override_and_skips_database(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Preflight should not touch the database and should honor --device."""
    worker_main = _load_worker_main_module()
    monkeypatch.setattr(
        worker_main,
        "get_settings",
        lambda: SimpleNamespace(
            device_preference="auto",
            diarization_model_id="pyannote/test-model",
            huggingface_token="hf-token",
        ),
    )
    monkeypatch.setattr(
        worker_main,
        "inspect_worker_runtime",
        lambda **kwargs: _runtime_report(
            requested_device=kwargs["requested_device"],
            ready=True,
        ),
    )

    exit_code = worker_main.main(["--preflight", "--device", "cpu"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "Worker runtime preflight: READY" in captured.out
    assert "Requested device: cpu" in captured.out


def test_main_preflight_returns_nonzero_when_runtime_is_not_ready(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Preflight should fail fast with a nonzero exit code when readiness is missing."""
    worker_main = _load_worker_main_module()
    monkeypatch.setattr(
        worker_main,
        "get_settings",
        lambda: SimpleNamespace(
            device_preference="cuda",
            diarization_model_id="pyannote/test-model",
            huggingface_token="hf-token",
        ),
    )
    monkeypatch.setattr(
        worker_main,
        "inspect_worker_runtime",
        lambda **kwargs: _runtime_report(
            requested_device=kwargs["requested_device"],
            ready=False,
        ),
    )

    exit_code = worker_main.main(["--preflight"])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "Worker runtime preflight: NOT READY" in captured.out


def test_main_rejects_device_without_preflight() -> None:
    """Device overrides are only valid together with preflight."""
    worker_main = _load_worker_main_module()
    with pytest.raises(SystemExit):
        worker_main.main(["--device", "cpu"])


def test_main_rejects_preflight_and_once_together() -> None:
    """CLI modes should stay mutually exclusive."""
    worker_main = _load_worker_main_module()
    with pytest.raises(SystemExit):
        worker_main.main(["--preflight", "--once"])


def test_main_preflight_isolated_from_deferred_db_and_service_imports(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Preflight should work even if deferred DB/service imports would fail."""
    monkeypatch.delitem(sys.modules, "app.db.config", raising=False)
    monkeypatch.delitem(sys.modules, "app.worker.service", raising=False)

    worker_main = _load_worker_main_module()
    monkeypatch.setattr(
        worker_main,
        "get_settings",
        lambda: SimpleNamespace(
            device_preference="cpu",
            diarization_model_id="pyannote/test-model",
            huggingface_token="hf-token",
        ),
    )
    monkeypatch.setattr(
        worker_main,
        "inspect_worker_runtime",
        lambda **kwargs: _runtime_report(
            requested_device=kwargs["requested_device"],
            ready=True,
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "app.db.config",
        _make_module(
            "app.db.config",
            create_session_factory=lambda: (_ for _ in ()).throw(
                AssertionError("preflight should not import app.db.config")
            ),
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "app.worker.service",
        _make_module(
            "app.worker.service",
            run_worker_once=lambda **kwargs: (_ for _ in ()).throw(
                AssertionError("preflight should not import app.worker.service")
            ),
            run_worker_forever=lambda **kwargs: (_ for _ in ()).throw(
                AssertionError("preflight should not import app.worker.service")
            ),
        ),
    )

    exit_code = worker_main.main(["--preflight", "--device", "cpu"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "Worker runtime preflight: READY" in captured.out


def test_main_once_uses_deferred_db_and_service_imports(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Non-preflight execution should use deferred DB/service imports normally."""
    calls: dict[str, object] = {}
    monkeypatch.delitem(sys.modules, "app.db.config", raising=False)
    monkeypatch.delitem(sys.modules, "app.worker.service", raising=False)
    monkeypatch.delitem(sys.modules, "app.worker.cleanup", raising=False)

    worker_main = _load_worker_main_module()
    settings = SimpleNamespace(
        device_preference="auto",
        diarization_model_id="pyannote/test-model",
        huggingface_token="hf-token",
    )
    monkeypatch.setattr(worker_main, "get_settings", lambda: settings)

    def _run_cleanup(**kwargs: object) -> str:
        calls["cleanup"] = kwargs
        return "cleanup-report"

    def _log_cleanup(**kwargs: object) -> None:
        calls["cleanup_log"] = kwargs

    monkeypatch.setitem(
        sys.modules,
        "app.db.config",
        _make_module(
            "app.db.config",
            create_session_factory=lambda: "fake-session-factory",
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "app.worker.cleanup",
        _make_module(
            "app.worker.cleanup",
            run_storage_cleanup=_run_cleanup,
            log_storage_cleanup_report=_log_cleanup,
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "app.worker.service",
        _make_module(
            "app.worker.service",
            run_worker_once=lambda **kwargs: calls.update(kwargs),
            run_worker_forever=lambda **kwargs: (_ for _ in ()).throw(
                AssertionError("continuous worker mode should not run in this test")
            ),
        ),
    )

    exit_code = worker_main.main(["--once"])

    assert exit_code == 0
    assert calls["cleanup"] == {
        "session_factory": "fake-session-factory",
        "settings": settings,
    }
    assert calls["cleanup_log"] == {
        "logger": worker_main.LOGGER,
        "trigger": "startup",
        "report": "cleanup-report",
    }
    assert calls["session_factory"] == "fake-session-factory"
    assert calls["settings"] == settings
