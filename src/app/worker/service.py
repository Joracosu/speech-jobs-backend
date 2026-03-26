"""Worker lifecycle helpers for claiming and processing jobs."""

from __future__ import annotations

from pathlib import Path
from time import sleep

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.core.settings import Settings, get_settings
from app.db import Job, JobResult, JobStatus, create_session_factory, utcnow


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


def process_claimed_job(
    session_factory: sessionmaker[Session],
    job_id: int,
    settings: Settings,
) -> None:
    """Process a claimed job with deterministic placeholder behavior."""
    try:
        with session_factory() as session, session.begin():
            job = session.get(Job, job_id)
            if job is None:
                return

            input_path = Path(job.stored_path)
            if not input_path.exists():
                job.status = JobStatus.FAILED
                job.completed_at = utcnow()
                job.error_code = "missing_input_file"
                job.error_message = (
                    f"Input file does not exist at '{job.stored_path}'."
                )
                return

            job.result = JobResult(
                transcript_text="",
                transcript_json={"segments": [], "processor": "placeholder"},
                speaker_segments_json=None,
                detected_language=None,
                metadata_json={
                    "mode": "placeholder",
                    "worker_id": settings.worker_id,
                },
            )
            job.status = JobStatus.COMPLETED
            job.completed_at = utcnow()
            job.error_code = None
            job.error_message = None
    except Exception as exc:
        _mark_job_failed(
            session_factory=session_factory,
            job_id=job_id,
            error_code="processing_error",
            error_message=f"Placeholder worker processing failed: {exc}",
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
