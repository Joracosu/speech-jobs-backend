"""Tests for the worker-side silence inspection helper."""

from __future__ import annotations

from pathlib import Path
import math
import wave

import pytest

from app.worker.silence import SilenceClassification, inspect_audio_silence


def _write_wav(path: Path, frames: bytes) -> None:
    """Persist one small mono WAV file for silence-helper tests."""
    with wave.open(str(path), "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(16000)
        wav_file.writeframes(frames)


def _build_silent_frames(sample_count: int) -> bytes:
    """Return raw 16-bit PCM frames for silence."""
    return b"\x00\x00" * sample_count


def _build_tone_frames(sample_count: int) -> bytes:
    """Return raw 16-bit PCM frames for a short sine tone."""
    frames = bytearray()
    for sample_index in range(sample_count):
        amplitude = int(12000 * math.sin(2 * math.pi * 440 * sample_index / 16000))
        frames.extend(int(amplitude).to_bytes(2, byteorder="little", signed=True))
    return bytes(frames)


def test_inspect_audio_silence_classifies_real_silent_wav(tmp_path: Path) -> None:
    """A silent WAV should be positively classified as silence."""
    audio_path = tmp_path / "silent.wav"
    _write_wav(audio_path, _build_silent_frames(16000))

    result = inspect_audio_silence(audio_path)

    assert result.classification == SilenceClassification.SILENCE


def test_inspect_audio_silence_does_not_flag_short_real_signal_as_silence(
    tmp_path: Path,
) -> None:
    """A short WAV with real signal should not be classified as silence."""
    audio_path = tmp_path / "signal.wav"
    _write_wav(audio_path, _build_tone_frames(4800))

    result = inspect_audio_silence(audio_path)

    assert result.classification == SilenceClassification.NOT_SILENCE


def test_inspect_audio_silence_returns_inconclusive_on_operational_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Operational errors should fail open as inconclusive."""
    audio_path = tmp_path / "input.wav"
    _write_wav(audio_path, _build_silent_frames(16000))

    monkeypatch.setattr(
        "app.worker.silence._probe_audio_duration",
        lambda *_: (_ for _ in ()).throw(RuntimeError("ffprobe failed during silence precheck.")),
    )

    result = inspect_audio_silence(audio_path)

    assert result.classification == SilenceClassification.INCONCLUSIVE
    assert result.detail == "ffprobe failed during silence precheck."
