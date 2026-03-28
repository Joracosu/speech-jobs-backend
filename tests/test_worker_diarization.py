"""Tests for the pyannote.audio adapter normalization boundary."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from app.worker.diarization import DiarizationExecutionError, diarize_audio
from app.worker.runtime_checks import ComponentRuntimeStatus, RuntimeIssue


class FakeAnnotation:
    """Minimal fake annotation object with itertracks support."""

    def __init__(self, tracks: list[tuple[SimpleNamespace, object, str]]) -> None:
        self._tracks = tracks

    def itertracks(
        self,
        yield_label: bool = False,
    ) -> list[tuple[SimpleNamespace, object, str]]:
        assert yield_label is True
        return self._tracks


class FakePipeline:
    """Minimal fake diarization pipeline for adapter tests."""

    def __init__(self, output: object) -> None:
        self._output = output

    def __call__(self, _: object) -> object:
        return self._output


def _ready_runtime_status(requested_device: str, resolved_device: str) -> ComponentRuntimeStatus:
    """Build a ready diarization runtime status for adapter tests."""
    return ComponentRuntimeStatus(
        component="diarization",
        requested_device=requested_device,
        resolved_device=resolved_device,
        ready=True,
        issues=(),
    )


def test_diarize_audio_normalizes_sorts_and_discards_invalid_segments(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The adapter should freeze the speaker segment contract deterministically."""
    audio_path = tmp_path / "input.wav"
    audio_path.write_bytes(b"audio")

    annotation = FakeAnnotation(
        tracks=[
            (SimpleNamespace(start=3.0, end=4.0), object(), "speaker_b"),
            (SimpleNamespace(start=1.0, end=2.0), object(), "speaker_a"),
            (SimpleNamespace(start=-1.0, end=0.2), object(), "speaker_x"),
            (SimpleNamespace(start=2.0, end=2.0), object(), "speaker_y"),
            (SimpleNamespace(start=1.0, end=1.5), object(), "speaker_b"),
        ]
    )
    fake_pipeline = FakePipeline(
        SimpleNamespace(speaker_diarization=annotation)
    )

    monkeypatch.setattr(
        "app.worker.diarization.inspect_diarization_runtime",
        lambda *args, **kwargs: _ready_runtime_status("cpu", "cpu"),
    )
    monkeypatch.setattr(
        "app.worker.diarization._get_cached_pipeline",
        lambda *args: fake_pipeline,
    )
    monkeypatch.setattr(
        "app.worker.diarization._send_pipeline_to_device",
        lambda *args: None,
    )
    monkeypatch.setattr(
        "app.worker.diarization._load_audio_input",
        lambda *args: {"waveform": object(), "sample_rate": 16000},
    )

    result = diarize_audio(
        audio_path=audio_path,
        requested_device="cpu",
        model_id="pyannote/test-model",
        huggingface_token="hf-token",
    )

    assert result.speaker_segments_json == [
        {"speaker": "speaker_b", "start": 1.0, "end": 1.5},
        {"speaker": "speaker_a", "start": 1.0, "end": 2.0},
        {"speaker": "speaker_b", "start": 3.0, "end": 4.0},
    ]
    assert result.metadata_json == {
        "diarization_enabled": True,
        "diarization_model_id": "pyannote/test-model",
        "diarization_device": "cpu",
        "speaker_count": 2,
    }


def test_diarize_audio_can_finish_successfully_with_empty_segments(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Discarding every invalid segment should still produce a successful empty list."""
    audio_path = tmp_path / "input.wav"
    audio_path.write_bytes(b"audio")

    annotation = FakeAnnotation(
        tracks=[
            (SimpleNamespace(start=-1.0, end=0.0), object(), "speaker_a"),
            (SimpleNamespace(start=3.0, end=2.0), object(), "speaker_b"),
        ]
    )
    fake_pipeline = FakePipeline(annotation)

    monkeypatch.setattr(
        "app.worker.diarization.inspect_diarization_runtime",
        lambda *args, **kwargs: _ready_runtime_status("cpu", "cpu"),
    )
    monkeypatch.setattr(
        "app.worker.diarization._get_cached_pipeline",
        lambda *args: fake_pipeline,
    )
    monkeypatch.setattr(
        "app.worker.diarization._send_pipeline_to_device",
        lambda *args: None,
    )
    monkeypatch.setattr(
        "app.worker.diarization._load_audio_input",
        lambda *args: {"waveform": object(), "sample_rate": 16000},
    )

    result = diarize_audio(
        audio_path=audio_path,
        requested_device="cpu",
        model_id="pyannote/test-model",
        huggingface_token="hf-token",
    )

    assert result.speaker_segments_json == []
    assert result.metadata_json["speaker_count"] == 0


def test_diarize_audio_requires_huggingface_token(tmp_path: Path) -> None:
    """A missing token should fail early with a controlled, actionable error."""
    audio_path = tmp_path / "input.wav"
    audio_path.write_bytes(b"audio")

    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(
            "app.worker.diarization.inspect_diarization_runtime",
            lambda *args, **kwargs: ComponentRuntimeStatus(
                component="diarization",
                requested_device="cpu",
                resolved_device="cpu",
                ready=False,
                issues=(
                    RuntimeIssue(
                        component="diarization",
                        kind="config_missing",
                        message="HUGGINGFACE_TOKEN is required for diarization runtime.",
                    ),
                ),
            ),
        )

        with pytest.raises(
            DiarizationExecutionError,
            match="HUGGINGFACE_TOKEN is required for diarization runtime",
        ):
            diarize_audio(
                audio_path=audio_path,
                requested_device="cpu",
                model_id="pyannote/test-model",
                huggingface_token=None,
            )


def test_diarize_audio_uses_shared_runtime_issue_precedence(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Dependency issues should surface before any CUDA availability diagnosis."""
    audio_path = tmp_path / "input.wav"
    audio_path.write_bytes(b"audio")

    monkeypatch.setattr(
        "app.worker.diarization.inspect_diarization_runtime",
        lambda *args, **kwargs: ComponentRuntimeStatus(
            component="diarization",
            requested_device="cuda",
            resolved_device="cuda",
            ready=False,
            issues=(
                RuntimeIssue(
                    component="diarization",
                    kind="dependency_missing",
                    message="Diarization dependency 'torch' is unavailable: No module named 'torch'",
                ),
            ),
        ),
    )

    with pytest.raises(
        DiarizationExecutionError,
        match="Diarization dependency 'torch' is unavailable",
    ):
        diarize_audio(
            audio_path=audio_path,
            requested_device="cuda",
            model_id="pyannote/test-model",
            huggingface_token="hf-token",
        )


def test_diarize_audio_wraps_runtime_failures_as_controlled_errors(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Pipeline runtime failures should surface as diarization execution errors."""
    audio_path = tmp_path / "input.wav"
    audio_path.write_bytes(b"audio")

    class BrokenPipeline:
        def __call__(self, _: str) -> object:
            raise RuntimeError("diarization boom")

    monkeypatch.setattr(
        "app.worker.diarization.inspect_diarization_runtime",
        lambda *args, **kwargs: _ready_runtime_status("cpu", "cpu"),
    )
    monkeypatch.setattr(
        "app.worker.diarization._get_cached_pipeline",
        lambda *args: BrokenPipeline(),
    )
    monkeypatch.setattr(
        "app.worker.diarization._send_pipeline_to_device",
        lambda *args: None,
    )
    monkeypatch.setattr(
        "app.worker.diarization._load_audio_input",
        lambda *args: {"waveform": object(), "sample_rate": 16000},
    )

    with pytest.raises(DiarizationExecutionError, match="pyannote.audio diarization failed"):
        diarize_audio(
            audio_path=audio_path,
            requested_device="cpu",
            model_id="pyannote/test-model",
            huggingface_token="hf-token",
        )


def test_diarize_audio_wraps_audio_loading_failures_as_controlled_errors(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Local audio loading failures should surface as controlled diarization errors."""
    audio_path = tmp_path / "input.wav"
    audio_path.write_bytes(b"audio")

    monkeypatch.setattr(
        "app.worker.diarization.inspect_diarization_runtime",
        lambda *args, **kwargs: _ready_runtime_status("cpu", "cpu"),
    )
    monkeypatch.setattr(
        "app.worker.diarization._get_cached_pipeline",
        lambda *args: FakePipeline(object()),
    )
    monkeypatch.setattr(
        "app.worker.diarization._send_pipeline_to_device",
        lambda *args: None,
    )
    monkeypatch.setattr(
        "app.worker.diarization._load_audio_input",
        lambda *args: (_ for _ in ()).throw(
            DiarizationExecutionError("Unable to decode audio for diarization: boom")
        ),
    )

    with pytest.raises(
        DiarizationExecutionError,
        match="Unable to decode audio for diarization",
    ):
        diarize_audio(
            audio_path=audio_path,
            requested_device="cpu",
            model_id="pyannote/test-model",
            huggingface_token="hf-token",
        )
