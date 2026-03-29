"""Worker-side helpers for deterministic silence inspection."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
import re
import subprocess


FFPROBE_TIMEOUT_SECONDS = 10
FFMPEG_TIMEOUT_SECONDS = 15
SILENCE_NOISE_THRESHOLD_DB = "-50dB"
SILENCE_MIN_DURATION_SECONDS = "0.2"
SILENCE_EDGE_TOLERANCE_SECONDS = 0.05

_SILENCE_START_PATTERN = re.compile(r"silence_start:\s*(?P<value>\d+(?:\.\d+)?)")
_SILENCE_END_PATTERN = re.compile(r"silence_end:\s*(?P<value>\d+(?:\.\d+)?)")


class SilenceClassification(str, Enum):
    """Stable worker-internal silence classification outcomes."""

    SILENCE = "silence"
    NOT_SILENCE = "not_silence"
    INCONCLUSIVE = "inconclusive"


@dataclass(slots=True, frozen=True)
class SilenceInspectionResult:
    """Return shape for one deterministic silence precheck."""

    classification: SilenceClassification
    detail: str | None = None


def _probe_audio_duration(audio_path: Path) -> float:
    """Return the media duration used to validate full-file silence coverage."""
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(audio_path),
        ],
        capture_output=True,
        text=True,
        timeout=FFPROBE_TIMEOUT_SECONDS,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError("ffprobe failed during silence precheck.")

    duration_text = result.stdout.strip()
    if not duration_text:
        raise RuntimeError("ffprobe did not return a duration for silence precheck.")

    try:
        duration = float(duration_text)
    except ValueError as exc:
        raise RuntimeError("ffprobe returned a non-numeric duration.") from exc

    if duration <= 0:
        raise RuntimeError("ffprobe returned a non-positive duration.")

    return duration


def _run_silencedetect(audio_path: Path) -> str:
    """Return ffmpeg silencedetect diagnostics from stderr."""
    result = subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-nostats",
            "-i",
            str(audio_path),
            "-af",
            f"silencedetect=noise={SILENCE_NOISE_THRESHOLD_DB}:d={SILENCE_MIN_DURATION_SECONDS}",
            "-f",
            "null",
            "-",
        ],
        capture_output=True,
        text=True,
        timeout=FFMPEG_TIMEOUT_SECONDS,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError("ffmpeg silencedetect failed during silence precheck.")
    return result.stderr


def inspect_audio_silence(audio_path: Path) -> SilenceInspectionResult:
    """Classify whether one audio file is confidently silence.

    The precheck is intentionally fail-open: any operational uncertainty returns
    ``INCONCLUSIVE`` so the worker can continue through normal ASR.
    """

    try:
        duration_seconds = _probe_audio_duration(audio_path)
        silencedetect_output = _run_silencedetect(audio_path)
    except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
        return SilenceInspectionResult(
            classification=SilenceClassification.INCONCLUSIVE,
            detail=str(exc),
        )

    silence_starts = [
        float(match.group("value"))
        for match in _SILENCE_START_PATTERN.finditer(silencedetect_output)
    ]
    silence_ends = [
        float(match.group("value"))
        for match in _SILENCE_END_PATTERN.finditer(silencedetect_output)
    ]

    if not silence_starts and not silence_ends:
        return SilenceInspectionResult(SilenceClassification.NOT_SILENCE)

    if len(silence_starts) != 1 or len(silence_ends) != 1:
        return SilenceInspectionResult(SilenceClassification.NOT_SILENCE)

    silence_start = silence_starts[0]
    silence_end = silence_ends[0]
    if (
        silence_start <= SILENCE_EDGE_TOLERANCE_SECONDS
        and silence_end >= duration_seconds - SILENCE_EDGE_TOLERANCE_SECONDS
    ):
        return SilenceInspectionResult(SilenceClassification.SILENCE)

    return SilenceInspectionResult(SilenceClassification.NOT_SILENCE)
