"""ASR adapter boundary for worker-side transcription."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.worker.runtime_checks import get_primary_issue, inspect_asr_runtime


LANGUAGE_PROBABILITY_THRESHOLD = 0.5
PROFILE_TO_MODEL = {
    "fast": "base",
    "balanced": "small",
    "accurate": "medium",
}
ENGINE_NAME = "faster-whisper"


class AsrExecutionError(Exception):
    """Represent a controlled ASR execution failure."""


@dataclass(slots=True, frozen=True)
class AsrTranscriptionResult:
    """Normalized ASR output returned by the adapter."""

    transcript_text: str
    transcript_json: dict[str, Any]
    detected_language: str | None
    metadata_json: dict[str, Any]

def _resolve_model_name(profile: str) -> str:
    """Return the model name for the public processing profile."""
    try:
        return PROFILE_TO_MODEL[profile]
    except KeyError as exc:
        raise AsrExecutionError(f"Unsupported ASR profile '{profile}'.") from exc


def _resolve_compute_type(resolved_device: str) -> str:
    """Return the fixed compute type for the resolved device."""
    if resolved_device == "cuda":
        return "float16"
    return "int8"


@lru_cache(maxsize=12)
def _get_cached_model(model_name: str, resolved_device: str, compute_type: str) -> Any:
    """Load and cache a Whisper model instance for the current process."""
    try:
        from faster_whisper import WhisperModel
    except Exception as exc:
        raise AsrExecutionError(
            f"Unable to import {ENGINE_NAME}: {exc}"
        ) from exc

    try:
        return WhisperModel(
            model_name,
            device=resolved_device,
            compute_type=compute_type,
        )
    except Exception as exc:
        raise AsrExecutionError(
            f"Unable to load {ENGINE_NAME} model '{model_name}': {exc}"
        ) from exc


def _normalize_detected_language(info: Any) -> str | None:
    """Return a reliable language code or None."""
    language = getattr(info, "language", None)
    language_probability = getattr(info, "language_probability", None)

    if not language or not isinstance(language, str):
        return None
    if language_probability is None:
        return language
    if language_probability >= LANGUAGE_PROBABILITY_THRESHOLD:
        return language
    return None


def _normalize_segment(raw_segment: Any) -> dict[str, int | float | str] | None:
    """Return one normalized segment or None if it is clearly invalid."""
    try:
        segment_id = int(getattr(raw_segment, "id"))
        start = float(getattr(raw_segment, "start"))
        end = float(getattr(raw_segment, "end"))
        text = str(getattr(raw_segment, "text", "")).strip()
    except Exception:
        return None

    if start < 0 or end < 0 or end < start:
        return None

    return {
        "id": segment_id,
        "start": start,
        "end": end,
        "text": text,
    }


def _segments_have_consistent_ids(
    segments: list[dict[str, int | float | str]],
) -> bool:
    """Return whether segment IDs can drive deterministic ordering."""
    ids = [int(segment["id"]) for segment in segments]
    return len(ids) == len(set(ids)) and all(segment_id >= 0 for segment_id in ids)


def _sort_segments(
    segments: list[dict[str, int | float | str]],
) -> list[dict[str, int | float | str]]:
    """Return a deterministically ordered list of normalized segments."""
    if _segments_have_consistent_ids(segments):
        return sorted(
            segments,
            key=lambda segment: (
                int(segment["id"]),
                float(segment["start"]),
                float(segment["end"]),
            ),
        )

    return sorted(
        segments,
        key=lambda segment: (
            float(segment["start"]),
            float(segment["end"]),
            int(segment["id"]),
        ),
    )


def _build_transcript_text(
    segments: list[dict[str, int | float | str]],
) -> str:
    """Build a deterministic transcript string from normalized ordered segments."""
    parts = [
        str(segment["text"]).strip()
        for segment in segments
        if str(segment["text"]).strip()
    ]
    return " ".join(parts)


def transcribe_audio(
    audio_path: Path,
    profile: str,
    requested_device: str,
) -> AsrTranscriptionResult:
    """Run ASR and return a normalized transcription result."""
    model_name = _resolve_model_name(profile)
    runtime_status = inspect_asr_runtime(requested_device)
    if not runtime_status.ready or runtime_status.resolved_device is None:
        issue = get_primary_issue(runtime_status)
        message = issue.message if issue is not None else "ASR runtime is not ready."
        raise AsrExecutionError(
            f"{message} Run 'python -m app.worker.main --preflight --device {requested_device}' "
            "to verify worker runtime readiness."
        )

    resolved_device = runtime_status.resolved_device
    compute_type = _resolve_compute_type(resolved_device)
    model = _get_cached_model(model_name, resolved_device, compute_type)

    try:
        raw_segments, info = model.transcribe(str(audio_path), task="transcribe")
    except Exception as exc:
        raise AsrExecutionError(
            f"{ENGINE_NAME} transcription failed: {exc}"
        ) from exc

    normalized_segments = [
        normalized
        for normalized in (
            _normalize_segment(raw_segment) for raw_segment in raw_segments
        )
        if normalized is not None
    ]
    ordered_segments = _sort_segments(normalized_segments)
    detected_language = _normalize_detected_language(info)
    transcript_text = _build_transcript_text(ordered_segments)
    empty_transcript = not bool(ordered_segments and transcript_text)

    return AsrTranscriptionResult(
        transcript_text=transcript_text,
        transcript_json={
            "segments": ordered_segments,
            "language": detected_language,
            "engine": ENGINE_NAME,
            "model": model_name,
        },
        detected_language=detected_language,
        metadata_json={
            "engine": ENGINE_NAME,
            "model": model_name,
            "requested_device": requested_device,
            "resolved_device": resolved_device,
            "compute_type": compute_type,
            "empty_transcript": empty_transcript,
        },
    )
