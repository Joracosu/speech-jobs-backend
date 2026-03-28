"""Worker lifecycle helpers for claiming and processing jobs."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from time import sleep

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.core.settings import Settings, get_settings
from app.db import Job, JobResult, JobStatus, create_session_factory, utcnow
from app.worker.asr import AsrExecutionError, AsrTranscriptionResult, transcribe_audio
from app.worker.diarization import (
    DiarizationExecutionError,
    DiarizationResult,
    diarize_audio,
)


LOGGER = logging.getLogger(__name__)


def claim_next_pending_job(session_factory: sessionmaker[Session]) -> int | None:
    """Claim the oldest pending job using a PostgreSQL-safe row lock."""
    with session_factory() as session, session.begin():
        statement = (
            select(Job)
            .where(Job.status == JobStatus.PENDING)
            .order_by(Job.created_at.asc(), Job.id.asc())
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        job = session.scalar(statement)
        if job is None:
            return None

        job.status = JobStatus.RUNNING
        job.started_at = utcnow()
        job.error_code = None
        job.error_message = None
        session.flush()
        return job.id


def _mark_job_failed(
    session_factory: sessionmaker[Session],
    job_id: int,
    error_code: str,
    error_message: str,
) -> None:
    """Persist a failed terminal state for the selected job."""
    with session_factory() as session, session.begin():
        job = session.get(Job, job_id)
        if job is None:
            return

        job.status = JobStatus.FAILED
        job.completed_at = utcnow()
        job.error_code = error_code
        job.error_message = error_message


@dataclass(slots=True, frozen=True)
class ClaimedJobContext:
    """Fields needed to run ASR outside the database transaction."""

    input_path: Path
    profile: str
    requested_device: str


def _build_completed_metadata(
    settings: Settings,
    profile: str,
    asr_result: AsrTranscriptionResult,
) -> dict[str, object]:
    """Return the shared metadata payload for a completed job."""
    return {
        "engine": asr_result.metadata_json["engine"],
        "profile": profile,
        "model": asr_result.metadata_json["model"],
        "requested_device": asr_result.metadata_json["requested_device"],
        "resolved_device": asr_result.metadata_json["resolved_device"],
        "compute_type": asr_result.metadata_json["compute_type"],
        "worker_id": settings.worker_id,
        "empty_transcript": asr_result.metadata_json["empty_transcript"],
    }


def _build_completed_metadata_with_diarization(
    settings: Settings,
    profile: str,
    asr_result: AsrTranscriptionResult,
    diarization_result: DiarizationResult,
) -> dict[str, object]:
    """Return the final metadata payload for a completed ASR+diarization job."""
    return {
        "mode": "asr+diarization",
        **_build_completed_metadata(
            settings=settings,
            profile=profile,
            asr_result=asr_result,
        ),
        "diarization_attempted": True,
        "diarization_status": "completed",
        "diarization_enabled": diarization_result.metadata_json["diarization_enabled"],
        "diarization_model_id": diarization_result.metadata_json["diarization_model_id"],
        "diarization_device": diarization_result.metadata_json["diarization_device"],
        "speaker_count": diarization_result.metadata_json["speaker_count"],
    }


def _build_completed_metadata_with_degraded_diarization(
    settings: Settings,
    profile: str,
    asr_result: AsrTranscriptionResult,
    diarization_error_message: str,
) -> dict[str, object]:
    """Return the final metadata payload for a completed ASR result with degraded diarization."""
    return {
        "mode": "asr",
        **_build_completed_metadata(
            settings=settings,
            profile=profile,
            asr_result=asr_result,
        ),
        "diarization_attempted": True,
        "diarization_status": "failed",
        "diarization_error_code": "diarization_error",
        "diarization_error_message": diarization_error_message,
    }


def _load_claimed_job_context(
    session_factory: sessionmaker[Session],
    job_id: int,
) -> ClaimedJobContext | None:
    """Load the claimed job and fail it early if preconditions are broken."""
    with session_factory() as session, session.begin():
        job = session.get(Job, job_id)
        if job is None:
            return None

        if job.result is not None:
            job.status = JobStatus.FAILED
            job.completed_at = utcnow()
            job.error_code = "processing_error"
            job.error_message = "Job already has a persisted result."
            return None

        input_path = Path(job.stored_path)
        if not input_path.exists():
            job.status = JobStatus.FAILED
            job.completed_at = utcnow()
            job.error_code = "missing_input_file"
            job.error_message = (
                f"Input file does not exist at '{job.stored_path}'."
            )
            return None

        return ClaimedJobContext(
            input_path=input_path,
            profile=job.profile_selected,
            requested_device=job.device_used,
        )


def _persist_completed_job(
    session_factory: sessionmaker[Session],
    job_id: int,
    transcript_text: str,
    transcript_json: dict[str, object],
    speaker_segments_json: list[dict[str, object]] | None,
    detected_language: str | None,
    metadata_json: dict[str, object],
) -> None:
    """Persist the completed job result and terminal state atomically."""
    with session_factory() as session, session.begin():
        job = session.get(Job, job_id)
        if job is None:
            return

        if job.result is not None:
            job.status = JobStatus.FAILED
            job.completed_at = utcnow()
            job.error_code = "processing_error"
            job.error_message = "Job already has a persisted result."
            return

        job.result = JobResult(
            transcript_text=transcript_text,
            transcript_json=transcript_json,
            speaker_segments_json=speaker_segments_json,
            detected_language=detected_language,
            metadata_json=metadata_json,
        )
        job.status = JobStatus.COMPLETED
        job.completed_at = utcnow()
        job.error_code = None
        job.error_message = None


def process_claimed_job(
    session_factory: sessionmaker[Session],
    job_id: int,
    settings: Settings,
) -> None:
    """Process one claimed job using real ASR and atomic terminal state updates."""
    try:
        job_context = _load_claimed_job_context(session_factory, job_id)
        if job_context is None:
            return

        LOGGER.info(
            "Processing claimed job id=%s requested_device=%s profile=%s",
            job_id,
            job_context.requested_device,
            job_context.profile,
        )

        asr_result = transcribe_audio(
            audio_path=job_context.input_path,
            profile=job_context.profile,
            requested_device=job_context.requested_device,
        )
        LOGGER.info(
            "ASR runtime ready for job id=%s requested_device=%s resolved_device=%s",
            job_id,
            job_context.requested_device,
            asr_result.metadata_json["resolved_device"],
        )
    except AsrExecutionError as exc:
        LOGGER.warning("ASR failed for job id=%s: %s", job_id, exc)
        _mark_job_failed(
            session_factory=session_factory,
            job_id=job_id,
            error_code="asr_error",
            error_message=str(exc),
        )
        return
    except Exception as exc:
        LOGGER.warning("Worker processing failed for job id=%s: %s", job_id, exc)
        _mark_job_failed(
            session_factory=session_factory,
            job_id=job_id,
            error_code="processing_error",
            error_message=f"Worker processing failed: {exc}",
        )
        return

    try:
        diarization_result = diarize_audio(
            audio_path=job_context.input_path,
            requested_device=job_context.requested_device,
            model_id=settings.diarization_model_id,
            huggingface_token=settings.huggingface_token,
        )
        LOGGER.info(
            "Diarization runtime ready for job id=%s requested_device=%s resolved_device=%s",
            job_id,
            job_context.requested_device,
            diarization_result.metadata_json["diarization_device"],
        )
    except DiarizationExecutionError as exc:
        LOGGER.warning(
            "Diarization failed for job id=%s, preserving ASR result: %s",
            job_id,
            exc,
        )
        speaker_segments_json: list[dict[str, object]] | None = None
        metadata_json = _build_completed_metadata_with_degraded_diarization(
            settings=settings,
            profile=job_context.profile,
            asr_result=asr_result,
            diarization_error_message=str(exc),
        )
    except Exception as exc:
        LOGGER.warning("Unexpected diarization processing failed for job id=%s: %s", job_id, exc)
        _mark_job_failed(
            session_factory=session_factory,
            job_id=job_id,
            error_code="processing_error",
            error_message=f"Worker processing failed: {exc}",
        )
        return
    else:
        speaker_segments_json = [
            dict(segment) for segment in diarization_result.speaker_segments_json
        ]
        metadata_json = _build_completed_metadata_with_diarization(
            settings=settings,
            profile=job_context.profile,
            asr_result=asr_result,
            diarization_result=diarization_result,
        )

    try:
        _persist_completed_job(
            session_factory=session_factory,
            job_id=job_id,
            transcript_text=asr_result.transcript_text,
            transcript_json=asr_result.transcript_json,
            speaker_segments_json=speaker_segments_json,
            detected_language=asr_result.detected_language,
            metadata_json=metadata_json,
        )
        LOGGER.info(
            "Completed job id=%s successfully with diarization_status=%s.",
            job_id,
            metadata_json["diarization_status"],
        )
    except Exception as exc:
        LOGGER.warning("Failed to persist result for job id=%s: %s", job_id, exc)
        _mark_job_failed(
            session_factory=session_factory,
            job_id=job_id,
            error_code="processing_error",
            error_message=f"Failed to persist processing result: {exc}",
        )


def run_worker_once(
    session_factory: sessionmaker[Session] | None = None,
    settings: Settings | None = None,
) -> bool:
    """Claim and process at most one pending job."""
    resolved_settings = settings or get_settings()
    resolved_session_factory = session_factory or create_session_factory()

    job_id = claim_next_pending_job(resolved_session_factory)
    if job_id is None:
        return False

    process_claimed_job(
        session_factory=resolved_session_factory,
        job_id=job_id,
        settings=resolved_settings,
    )
    return True


def run_worker_forever(
    session_factory: sessionmaker[Session] | None = None,
    settings: Settings | None = None,
) -> None:
    """Run the worker loop continuously using the configured poll interval."""
    resolved_settings = settings or get_settings()
    resolved_session_factory = session_factory or create_session_factory()

    while True:
        processed_job = run_worker_once(
            session_factory=resolved_session_factory,
            settings=resolved_settings,
        )
        if not processed_job:
            sleep(resolved_settings.worker_poll_interval_seconds)
