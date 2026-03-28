"""Diarization adapter boundary for worker-side speaker segmentation."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any
import warnings

import numpy

from app.worker.runtime_checks import get_primary_issue, inspect_diarization_runtime


ENGINE_NAME = "pyannote.audio"
_PIPELINE_CACHE: dict[str, Any] = {}


class DiarizationExecutionError(Exception):
    """Represent a controlled diarization execution failure."""


@dataclass(slots=True, frozen=True)
class DiarizationResult:
    """Normalized diarization output returned by the adapter."""

    speaker_segments_json: list[dict[str, str | float]]
    metadata_json: dict[str, Any]


def _load_pipeline(model_id: str, huggingface_token: str) -> Any:
    """Load a diarization pipeline from pyannote.audio."""
    try:
        with warnings.catch_warnings():
            warnings.filterwarnings(
                "ignore",
                category=UserWarning,
                module=r"pyannote\.audio\.core\.io",
            )
            warnings.filterwarnings(
                "ignore",
                message=r".*torchcodec is not installed correctly so built-in audio decoding will fail.*",
                category=UserWarning,
            )
            from pyannote.audio import Pipeline
    except Exception as exc:
        raise DiarizationExecutionError(
            f"Unable to import {ENGINE_NAME}: {exc}"
        ) from exc

    try:
        return Pipeline.from_pretrained(model_id, token=huggingface_token)
    except TypeError:
        try:
            return Pipeline.from_pretrained(
                model_id,
                use_auth_token=huggingface_token,
            )
        except Exception as exc:
            raise DiarizationExecutionError(
                f"Unable to load {ENGINE_NAME} pipeline '{model_id}': {exc}"
            ) from exc
    except Exception as exc:
        raise DiarizationExecutionError(
            f"Unable to load {ENGINE_NAME} pipeline '{model_id}': {exc}"
        ) from exc


def _get_cached_pipeline(model_id: str, huggingface_token: str) -> Any:
    """Load and cache a diarization pipeline for the current process."""
    pipeline = _PIPELINE_CACHE.get(model_id)
    if pipeline is None:
        pipeline = _load_pipeline(model_id, huggingface_token)
        _PIPELINE_CACHE[model_id] = pipeline
    return pipeline


def _send_pipeline_to_device(pipeline: Any, resolved_device: str) -> None:
    """Move the loaded pipeline to the resolved runtime device."""
    try:
        import torch
    except Exception as exc:
        raise DiarizationExecutionError(
            f"Unable to import torch for diarization device management: {exc}"
        ) from exc

    try:
        pipeline.to(torch.device(resolved_device))
    except Exception as exc:
        raise DiarizationExecutionError(
            f"Unable to move diarization pipeline to '{resolved_device}': {exc}"
        ) from exc


def _load_audio_input(audio_path: Path) -> dict[str, Any]:
    """Load audio explicitly so diarization does not depend on torchcodec decoding."""
    try:
        import av
    except Exception as exc:
        raise DiarizationExecutionError(
            f"Unable to import av for diarization audio loading: {exc}"
        ) from exc

    try:
        import torch
    except Exception as exc:
        raise DiarizationExecutionError(
            f"Unable to import torch for diarization audio loading: {exc}"
        ) from exc

    try:
        container = av.open(str(audio_path))
    except Exception as exc:
        raise DiarizationExecutionError(
            f"Unable to open audio for diarization: {exc}"
        ) from exc

    try:
        audio_stream = next(
            (stream for stream in container.streams if stream.type == "audio"),
            None,
        )
        if audio_stream is None:
            raise DiarizationExecutionError(
                "Unable to find an audio stream for diarization input."
            )

        sample_rate = int(audio_stream.rate or 0)
        decoded_frames: list[numpy.ndarray] = []
        for frame in container.decode(audio_stream):
            frame_samples = frame.to_ndarray()
            if frame_samples.ndim == 1:
                frame_samples = frame_samples.reshape(1, -1)
            decoded_frames.append(frame_samples.astype("float32", copy=False))
            if frame.sample_rate:
                sample_rate = int(frame.sample_rate)
    except DiarizationExecutionError:
        raise
    except Exception as exc:
        raise DiarizationExecutionError(
            f"Unable to decode audio for diarization: {exc}"
        ) from exc
    finally:
        container.close()

    if not decoded_frames or sample_rate <= 0:
        raise DiarizationExecutionError(
            "Unable to decode any audio samples for diarization input."
        )

    waveform = torch.from_numpy(numpy.concatenate(decoded_frames, axis=1))
    return {
        "waveform": waveform,
        "sample_rate": sample_rate,
    }


def _extract_annotation(output: Any) -> Any:
    """Return the annotation object from the diarization pipeline output."""
    return getattr(output, "speaker_diarization", output)


def _normalize_segment(turn: Any, speaker: Any) -> dict[str, str | float] | None:
    """Return one normalized speaker segment or None if it is clearly invalid."""
    try:
        normalized_speaker = str(speaker).strip()
        start = float(getattr(turn, "start"))
        end = float(getattr(turn, "end"))
    except Exception:
        return None

    if not normalized_speaker or start < 0 or end <= start:
        return None

    return {
        "speaker": normalized_speaker,
        "start": start,
        "end": end,
    }


def _sort_segments(
    segments: list[dict[str, str | float]],
) -> list[dict[str, str | float]]:
    """Return a deterministically ordered list of normalized speaker segments."""
    return sorted(
        segments,
        key=lambda segment: (
            float(segment["start"]),
            float(segment["end"]),
            str(segment["speaker"]),
        ),
    )


def diarize_audio(
    audio_path: Path,
    requested_device: str,
    model_id: str,
    huggingface_token: str | None,
) -> DiarizationResult:
    """Run diarization and return a normalized speaker-segment result."""
    runtime_status = inspect_diarization_runtime(
        requested_device,
        model_id=model_id,
        huggingface_token=huggingface_token,
    )
    if not runtime_status.ready or runtime_status.resolved_device is None:
        issue = get_primary_issue(runtime_status)
        message = (
            issue.message
            if issue is not None
            else "Diarization runtime is not ready."
        )
        raise DiarizationExecutionError(
            f"{message} Run 'python -m app.worker.main --preflight --device {requested_device}' "
            "to verify worker runtime readiness."
        )

    resolved_device = runtime_status.resolved_device
    pipeline = _get_cached_pipeline(model_id, huggingface_token)
    _send_pipeline_to_device(pipeline, resolved_device)
    audio_input = _load_audio_input(audio_path)

    try:
        output = pipeline(audio_input)
    except Exception as exc:
        raise DiarizationExecutionError(
            f"{ENGINE_NAME} diarization failed: {exc}"
        ) from exc

    annotation = _extract_annotation(output)

    try:
        raw_tracks = annotation.itertracks(yield_label=True)
    except Exception as exc:
        raise DiarizationExecutionError(
            f"Unable to read diarization segments from {ENGINE_NAME}: {exc}"
        ) from exc

    normalized_segments = [
        normalized
        for normalized in (
            _normalize_segment(turn, speaker)
            for turn, _, speaker in raw_tracks
        )
        if normalized is not None
    ]
    ordered_segments = _sort_segments(normalized_segments)

    return DiarizationResult(
        speaker_segments_json=ordered_segments,
        metadata_json={
            "diarization_enabled": True,
            "diarization_model_id": model_id,
            "diarization_device": resolved_device,
            "speaker_count": len({segment["speaker"] for segment in ordered_segments}),
        },
    )
