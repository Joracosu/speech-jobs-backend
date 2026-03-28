"""Tests for shared worker runtime readiness checks."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.worker.runtime_checks import (
    ISSUE_CONFIG_MISSING,
    ISSUE_CUDA_UNAVAILABLE,
    ISSUE_DEPENDENCY_MISSING,
    ISSUE_RUNTIME_ERROR,
    format_worker_runtime_report,
    get_primary_issue,
    inspect_asr_runtime,
    inspect_diarization_runtime,
    inspect_worker_runtime,
)


def test_inspect_asr_runtime_reports_missing_ctranslate2_before_cuda(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Missing ASR dependencies should win over any CUDA diagnosis."""
    monkeypatch.setattr(
        "app.worker.runtime_checks._import_ctranslate2",
        lambda: (_ for _ in ()).throw(ModuleNotFoundError("No module named 'ctranslate2'")),
    )

    status = inspect_asr_runtime("cuda")

    assert status.ready is False
    issue = get_primary_issue(status)
    assert issue is not None
    assert issue.kind == ISSUE_DEPENDENCY_MISSING
    assert "ctranslate2" in issue.message


def test_inspect_asr_runtime_auto_falls_back_to_cpu_when_cuda_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ASR auto mode should remain ready on CPU when CUDA is absent."""
    monkeypatch.setattr(
        "app.worker.runtime_checks._import_ctranslate2",
        lambda: SimpleNamespace(get_cuda_device_count=lambda: 0),
    )
    monkeypatch.setattr(
        "app.worker.runtime_checks._import_faster_whisper",
        lambda: object(),
    )

    status = inspect_asr_runtime("auto")

    assert status.ready is True
    assert status.resolved_device == "cpu"
    assert status.issues == ()


def test_inspect_diarization_runtime_reports_missing_torch_before_cuda(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Missing torch must not be collapsed into a CUDA-unavailable diagnosis."""
    monkeypatch.setattr(
        "app.worker.runtime_checks._import_torch",
        lambda: (_ for _ in ()).throw(ModuleNotFoundError("No module named 'torch'")),
    )

    status = inspect_diarization_runtime(
        "cuda",
        model_id="pyannote/test-model",
        huggingface_token="hf-token",
    )

    assert status.ready is False
    issue = get_primary_issue(status)
    assert issue is not None
    assert issue.kind == ISSUE_DEPENDENCY_MISSING
    assert "torch" in issue.message


def test_inspect_diarization_runtime_reports_missing_config_before_cuda(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Missing diarization config should be reported before CUDA checks."""
    monkeypatch.setattr(
        "app.worker.runtime_checks._import_torch",
        lambda: SimpleNamespace(cuda=SimpleNamespace(is_available=lambda: True, device_count=lambda: 1)),
    )
    monkeypatch.setattr(
        "app.worker.runtime_checks._import_pyannote_audio",
        lambda: object(),
    )
    monkeypatch.setattr(
        "app.worker.runtime_checks._validate_diarization_model_access",
        lambda *args, **kwargs: None,
    )

    status = inspect_diarization_runtime(
        "cuda",
        model_id="pyannote/test-model",
        huggingface_token=None,
    )

    assert status.ready is False
    issue = get_primary_issue(status)
    assert issue is not None
    assert issue.kind == ISSUE_CONFIG_MISSING
    assert "HUGGINGFACE_TOKEN" in issue.message


def test_inspect_diarization_runtime_reports_cuda_unavailable_only_after_dependencies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """CUDA-unavailable should only appear once dependencies and config are present."""
    monkeypatch.setattr(
        "app.worker.runtime_checks._import_torch",
        lambda: SimpleNamespace(cuda=SimpleNamespace(is_available=lambda: False, device_count=lambda: 0)),
    )
    monkeypatch.setattr(
        "app.worker.runtime_checks._import_pyannote_audio",
        lambda: object(),
    )
    monkeypatch.setattr(
        "app.worker.runtime_checks._validate_diarization_model_access",
        lambda *args, **kwargs: None,
    )

    status = inspect_diarization_runtime(
        "cuda",
        model_id="pyannote/test-model",
        huggingface_token="hf-token",
    )

    assert status.ready is False
    issue = get_primary_issue(status)
    assert issue is not None
    assert issue.kind == ISSUE_CUDA_UNAVAILABLE
    assert "torch" in issue.message


def test_inspect_diarization_runtime_cpu_does_not_require_cuda(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """CPU preflight must not fail just because CUDA is absent."""
    monkeypatch.setattr(
        "app.worker.runtime_checks._import_torch",
        lambda: SimpleNamespace(cuda=SimpleNamespace(is_available=lambda: False, device_count=lambda: 0)),
    )
    monkeypatch.setattr(
        "app.worker.runtime_checks._import_pyannote_audio",
        lambda: object(),
    )
    monkeypatch.setattr(
        "app.worker.runtime_checks._validate_diarization_model_access",
        lambda *args, **kwargs: None,
    )

    status = inspect_diarization_runtime(
        "cpu",
        model_id="pyannote/test-model",
        huggingface_token="hf-token",
    )

    assert status.ready is True
    assert status.resolved_device == "cpu"


def test_inspect_worker_runtime_requires_all_components_for_global_readiness(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Global readiness should fail if any required component is not ready."""
    monkeypatch.setattr(
        "app.worker.runtime_checks._import_ctranslate2",
        lambda: SimpleNamespace(get_cuda_device_count=lambda: 1),
    )
    monkeypatch.setattr(
        "app.worker.runtime_checks._import_faster_whisper",
        lambda: object(),
    )
    monkeypatch.setattr(
        "app.worker.runtime_checks._import_torch",
        lambda: SimpleNamespace(cuda=SimpleNamespace(is_available=lambda: False, device_count=lambda: 0)),
    )
    monkeypatch.setattr(
        "app.worker.runtime_checks._import_pyannote_audio",
        lambda: object(),
    )
    monkeypatch.setattr(
        "app.worker.runtime_checks._validate_diarization_model_access",
        lambda *args, **kwargs: None,
    )

    report = inspect_worker_runtime(
        requested_device="cuda",
        model_id="pyannote/test-model",
        huggingface_token="hf-token",
    )

    assert report.ready is False
    assert report.asr.ready is True
    assert report.asr.resolved_device == "cuda"
    assert report.diarization.ready is False
    assert report.diarization.resolved_device == "cuda"

    formatted = format_worker_runtime_report(report)
    assert "Worker runtime preflight: NOT READY" in formatted
    assert "ASR: READY" in formatted
    assert "DIARIZATION: NOT READY" in formatted


def test_inspect_diarization_runtime_reports_inaccessible_model_as_runtime_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Model access problems should be reported before CUDA readiness."""
    monkeypatch.setattr(
        "app.worker.runtime_checks._import_torch",
        lambda: SimpleNamespace(cuda=SimpleNamespace(is_available=lambda: True, device_count=lambda: 1)),
    )
    monkeypatch.setattr(
        "app.worker.runtime_checks._import_pyannote_audio",
        lambda: object(),
    )
    monkeypatch.setattr(
        "app.worker.runtime_checks._validate_diarization_model_access",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            RuntimeError("Diarization model 'pyannote/test-model' is not accessible with the current HUGGINGFACE_TOKEN: 403 Forbidden")
        ),
    )

    status = inspect_diarization_runtime(
        "cuda",
        model_id="pyannote/test-model",
        huggingface_token="hf-token",
    )

    assert status.ready is False
    issue = get_primary_issue(status)
    assert issue is not None
    assert issue.kind == ISSUE_RUNTIME_ERROR
    assert "not accessible" in issue.message
