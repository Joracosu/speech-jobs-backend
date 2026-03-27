"""Worker lifecycle helpers for claiming and processing jobs."""

from __future__ import annotations

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
    diarization_result: DiarizationResult,
) -> dict[str, object]:
    """Return the final metadata payload for a completed ASR+diarization job."""
    return {
        "mode": "asr+diarization",
        "engine": asr_result.metadata_json["engine"],
        "profile": profile,
        "model": asr_result.metadata_json["model"],
        "requested_device": asr_result.metadata_json["requested_device"],
        "resolved_device": asr_result.metadata_json["resolved_device"],
        "compute_type": asr_result.metadata_json["compute_type"],
        "worker_id": settings.worker_id,
        "empty_transcript": asr_result.metadata_json["empty_transcript"],
        "diarization_enabled": diarization_result.metadata_json["diarization_enabled"],
        "diarization_model_id": diarization_result.metadata_json["diarization_model_id"],
        "diarization_device": diarization_result.metadata_json["diarization_device"],
        "speaker_count": diarization_result.metadata_json["speaker_count"],
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
    settings: Settings,
    asr_result: AsrTranscriptionResult,
    diarization_result: DiarizationResult,
) -> None:
    """Persist the completed ASR+diarization result and terminal state atomically."""
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
            transcript_text=asr_result.transcript_text,
            transcript_json=asr_result.transcript_json,
            speaker_segments_json=diarization_result.speaker_segments_json,
            detected_language=asr_result.detected_language,
            metadata_json=_build_completed_metadata(
                settings=settings,
                profile=job.profile_selected,
                asr_result=asr_result,
                diarization_result=diarization_result,
            ),
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

        asr_result = transcribe_audio(
            audio_path=job_context.input_path,
            profile=job_context.profile,
            requested_device=job_context.requested_device,
        )
        diarization_result = diarize_audio(
            audio_path=job_context.input_path,
            requested_device=job_context.requested_device,
            model_id=settings.diarization_model_id,
            huggingface_token=settings.huggingface_token,
        )
    except AsrExecutionError as exc:
        _mark_job_failed(
            session_factory=session_factory,
            job_id=job_id,
            error_code="asr_error",
            error_message=str(exc),
        )
        return
    except DiarizationExecutionError as exc:
        _mark_job_failed(
            session_factory=session_factory,
            job_id=job_id,
            error_code="diarization_error",
            error_message=str(exc),
        )
        return
    except Exception as exc:
        _mark_job_failed(
            session_factory=session_factory,
            job_id=job_id,
            error_code="processing_error",
            error_message=f"Worker processing failed: {exc}",
        )
        return

    try:
        _persist_completed_job(
            session_factory=session_factory,
            job_id=job_id,
            settings=settings,
            asr_result=asr_result,
            diarization_result=diarization_result,
        )
    except Exception as exc:
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
