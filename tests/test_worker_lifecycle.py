"""Tests for the dedicated worker lifecycle behavior."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import delete, select
from sqlalchemy.orm import Session, sessionmaker

from app.db.models import Job, JobResult, JobStatus
from app.worker.service import run_worker_once


@pytest.fixture()
def session_factory() -> sessionmaker[Session]:
    """Reuse the configured PostgreSQL session factory for worker tests."""
    from app.api.dependencies import get_session_factory

    return get_session_factory()


@pytest.fixture(autouse=True)
def clean_jobs_tables(session_factory: sessionmaker[Session]) -> Iterator[None]:
    """Keep jobs tables isolated even if a worker test fails midway."""
    with session_factory() as session:
        session.execute(delete(JobResult))
        session.execute(delete(Job))
        session.commit()

    try:
        yield
    finally:
        with session_factory() as session:
            session.execute(delete(JobResult))
            session.execute(delete(Job))
            session.commit()


def test_run_worker_once_returns_false_with_no_pending_jobs(
    session_factory: sessionmaker[Session],
) -> None:
    """Worker should exit cleanly when there are no pending jobs."""
    processed_job = run_worker_once(session_factory=session_factory)

    assert processed_job is False

    with session_factory() as session:
        assert session.scalar(select(Job.id).limit(1)) is None
        assert session.scalar(select(JobResult.id).limit(1)) is None


def test_run_worker_once_completes_pending_job_and_persists_placeholder_result(
    session_factory: sessionmaker[Session],
    tmp_path: Path,
) -> None:
    """Worker should process an existing input file into a completed placeholder job."""
    input_path = tmp_path / "input.wav"
    input_path.write_bytes(b"placeholder-audio")

    with session_factory() as session:
        job = Job(
            status=JobStatus.PENDING,
            original_filename="input.wav",
            stored_path=input_path.as_posix(),
            input_sha256="b" * 64,
            file_size_bytes=input_path.stat().st_size,
            media_duration_seconds=None,
            device_used="auto",
            profile_selected="balanced",
            config_snapshot={"profile": "balanced", "device_preference": "auto"},
            error_code=None,
            error_message=None,
        )
        session.add(job)
        session.commit()
        job_id = job.id

    processed_job = run_worker_once(session_factory=session_factory)

    assert processed_job is True

    with session_factory() as session:
        persisted_job = session.get(Job, job_id)
        assert persisted_job is not None
        assert persisted_job.status == JobStatus.COMPLETED
        assert persisted_job.started_at is not None
        assert persisted_job.completed_at is not None
        assert persisted_job.error_code is None
        assert persisted_job.error_message is None

        persisted_result = session.scalar(
            select(JobResult).where(JobResult.job_id == job_id)
        )
        assert persisted_result is not None
        assert persisted_result.transcript_text == ""
        assert persisted_result.transcript_json == {
            "segments": [],
            "processor": "placeholder",
        }
        assert persisted_result.speaker_segments_json is None
        assert persisted_result.detected_language is None
        assert persisted_result.metadata_json == {
            "mode": "placeholder",
            "worker_id": "local-worker-1",
        }


def test_run_worker_once_marks_job_failed_when_input_file_is_missing(
    session_factory: sessionmaker[Session],
    tmp_path: Path,
) -> None:
    """Worker should fail cleanly when the input path does not exist."""
    missing_input_path = tmp_path / "missing.wav"

    with session_factory() as session:
        job = Job(
            status=JobStatus.PENDING,
            original_filename="missing.wav",
            stored_path=missing_input_path.as_posix(),
            input_sha256="c" * 64,
            file_size_bytes=123,
            media_duration_seconds=None,
            device_used="auto",
            profile_selected="balanced",
            config_snapshot={"profile": "balanced", "device_preference": "auto"},
            error_code=None,
            error_message=None,
        )
        session.add(job)
        session.commit()
        job_id = job.id

    processed_job = run_worker_once(session_factory=session_factory)

    assert processed_job is True

    with session_factory() as session:
        persisted_job = session.get(Job, job_id)
        assert persisted_job is not None
        assert persisted_job.status == JobStatus.FAILED
        assert persisted_job.started_at is not None
        assert persisted_job.completed_at is not None
        assert persisted_job.error_code == "missing_input_file"
        assert persisted_job.error_message == (
            f"Input file does not exist at '{missing_input_path.as_posix()}'."
        )
        persisted_result = session.scalar(
            select(JobResult).where(JobResult.job_id == job_id)
        )
        assert persisted_result is None
