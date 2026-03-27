"""Tests for the faster-whisper adapter normalization boundary."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from app.worker.asr import AsrExecutionError, transcribe_audio


class FakeWhisperModel:
    """Minimal fake faster-whisper model for adapter tests."""

    def __init__(self, segments: list[SimpleNamespace], info: SimpleNamespace) -> None:
        self._segments = segments
        self._info = info

    def transcribe(self, _: str, task: str = "transcribe") -> tuple[list[SimpleNamespace], SimpleNamespace]:
        assert task == "transcribe"
        return self._segments, self._info


def test_transcribe_audio_orders_segments_and_builds_deterministic_text(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The adapter should normalize order before building transcript text."""
    audio_path = tmp_path / "input.wav"
    audio_path.write_bytes(b"audio")

    fake_model = FakeWhisperModel(
        segments=[
            SimpleNamespace(id=2, start=2.0, end=3.0, text="third"),
            SimpleNamespace(id=0, start=0.0, end=1.0, text="first"),
            SimpleNamespace(id=1, start=1.0, end=2.0, text="second"),
        ],
        info=SimpleNamespace(language="en", language_probability=0.9),
    )

    monkeypatch.setattr("app.worker.asr._has_cuda_available", lambda: False)
    monkeypatch.setattr("app.worker.asr._get_cached_model", lambda *args: fake_model)

    result = transcribe_audio(audio_path, profile="balanced", requested_device="cpu")

    assert result.transcript_text == "first second third"
    assert result.transcript_json["segments"] == [
        {"id": 0, "start": 0.0, "end": 1.0, "text": "first"},
        {"id": 1, "start": 1.0, "end": 2.0, "text": "second"},
        {"id": 2, "start": 2.0, "end": 3.0, "text": "third"},
    ]


def test_transcribe_audio_keeps_language_fields_consistent(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Reliable and unreliable language values should persist consistently."""
    audio_path = tmp_path / "input.wav"
    audio_path.write_bytes(b"audio")

    monkeypatch.setattr("app.worker.asr._has_cuda_available", lambda: False)

    reliable_model = FakeWhisperModel(
        segments=[SimpleNamespace(id=0, start=0.0, end=1.0, text="hello")],
        info=SimpleNamespace(language="en", language_probability=0.95),
    )
    monkeypatch.setattr("app.worker.asr._get_cached_model", lambda *args: reliable_model)
    reliable_result = transcribe_audio(
        audio_path,
        profile="balanced",
        requested_device="cpu",
    )
    assert reliable_result.detected_language == "en"
    assert reliable_result.transcript_json["language"] == "en"

    unreliable_model = FakeWhisperModel(
        segments=[SimpleNamespace(id=0, start=0.0, end=1.0, text="hola")],
        info=SimpleNamespace(language="es", language_probability=0.1),
    )
    monkeypatch.setattr(
        "app.worker.asr._get_cached_model", lambda *args: unreliable_model
    )
    unreliable_result = transcribe_audio(
        audio_path,
        profile="balanced",
        requested_device="cpu",
    )
    assert unreliable_result.detected_language is None
    assert unreliable_result.transcript_json["language"] is None


def test_transcribe_audio_discards_invalid_segments_and_can_finish_empty(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """All invalid segments should collapse into a valid empty transcript."""
    audio_path = tmp_path / "input.wav"
    audio_path.write_bytes(b"audio")

    fake_model = FakeWhisperModel(
        segments=[
            SimpleNamespace(id=0, start=-1.0, end=0.1, text="broken"),
            SimpleNamespace(id=1, start=2.0, end=1.0, text="also-broken"),
        ],
        info=SimpleNamespace(language=None, language_probability=None),
    )

    monkeypatch.setattr("app.worker.asr._has_cuda_available", lambda: False)
    monkeypatch.setattr("app.worker.asr._get_cached_model", lambda *args: fake_model)

    result = transcribe_audio(audio_path, profile="fast", requested_device="cpu")

    assert result.transcript_text == ""
    assert result.transcript_json["segments"] == []
    assert result.detected_language is None
    assert result.metadata_json["empty_transcript"] is True


def test_transcribe_audio_wraps_runtime_failures_as_asr_execution_error(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Model runtime failures should surface as the adapter's controlled error."""
    audio_path = tmp_path / "input.wav"
    audio_path.write_bytes(b"audio")

    class BrokenModel:
        def transcribe(self, *_: object, **__: object) -> None:
            raise RuntimeError("runtime boom")

    monkeypatch.setattr("app.worker.asr._has_cuda_available", lambda: False)
    monkeypatch.setattr("app.worker.asr._get_cached_model", lambda *args: BrokenModel())

    with pytest.raises(AsrExecutionError, match="faster-whisper transcription failed"):
        transcribe_audio(audio_path, profile="balanced", requested_device="cpu")
