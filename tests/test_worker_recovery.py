"""Tests for stale-running recovery and worker heartbeat behavior."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Event
from time import sleep
from types import SimpleNamespace

import pytest
from sqlalchemy import delete, select
from sqlalchemy.orm import Session, sessionmaker

from app.api.dependencies import get_session_factory
from app.db.models import Job, JobResult, JobStatus
from app.worker.asr import AsrTranscriptionResult
from app.worker.diarization import DiarizationResult
import app.worker.service as worker_service
from app.worker.service import (
    PROCESSING_INTERRUPTED_ERROR_MESSAGE,
    ReconciliationReport,
    _update_job_heartbeat,
    claim_next_pending_job,
    log_reconciliation_report,
    reconcile_stale_running_jobs,
    run_worker_once,
)
from app.worker.silence import SilenceClassification, SilenceInspectionResult


@pytest.fixture()
def session_factory() -> sessionmaker[Session]:
    """Reuse the configured PostgreSQL session factory for recovery tests."""
    return get_session_factory()


@pytest.fixture(autouse=True)
def clean_jobs_tables(session_factory: sessionmaker[Session]) -> Iterator[None]:
    """Keep job tables isolated before and after each recovery test."""
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


def _make_settings(**overrides: object) -> SimpleNamespace:
    """Return the minimal worker settings shape used by recovery tests."""
    values: dict[str, object] = {
        "worker_heartbeat_interval_seconds": 10,
        "worker_stale_after_seconds": 60,
        "worker_poll_interval_seconds": 0,
        "worker_cleanup_every_n_jobs": 10,
        "worker_id": "local-worker-1",
        "diarization_model_id": "pyannote/test-model",
        "huggingface_token": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _persist_job(
    session_factory: sessionmaker[Session],
    *,
    status: JobStatus,
    stored_path: str,
    started_at: datetime | None,
    last_heartbeat_at: datetime | None,
    completed_at: datetime | None = None,
    with_result: bool = False,
    error_code: str | None = None,
    error_message: str | None = None,
) -> int:
    """Persist one job row and optionally one linked result row."""
    updated_at = last_heartbeat_at or started_at or datetime(2026, 3, 29, 12, 0, tzinfo=UTC)
    with session_factory() as session:
        job = Job(
            status=status,
            created_at=updated_at,
            started_at=started_at,
            last_heartbeat_at=last_heartbeat_at,
            completed_at=completed_at,
            updated_at=updated_at,
            original_filename="input.wav",
            stored_path=stored_path,
            input_sha256="a" * 64,
            file_size_bytes=123,
            media_duration_seconds=None,
            device_used="cpu",
            profile_selected="balanced",
            config_snapshot={"profile": "balanced", "device_preference": "cpu"},
            error_code=error_code,
            error_message=error_message,
        )
        if with_result:
            job.result = JobResult(
                transcript_text="hello world",
                transcript_json={"segments": [], "language": "en"},
                speaker_segments_json=None,
                detected_language="en",
                metadata_json={"mode": "asr"},
            )
        session.add(job)
        session.commit()
        session.refresh(job)
        return job.id


def test_reconcile_stale_running_without_result_marks_failed(
    session_factory: sessionmaker[Session],
    tmp_path: Path,
) -> None:
    """A stale running job without result should reconcile to failed."""
    now = datetime(2026, 3, 29, 12, 0, tzinfo=UTC)
    input_path = tmp_path / "orphan.wav"
    input_path.write_bytes(b"audio")
    job_id = _persist_job(
        session_factory,
        status=JobStatus.RUNNING,
        stored_path=input_path.as_posix(),
        started_at=now - timedelta(minutes=3),
        last_heartbeat_at=now - timedelta(minutes=2),
    )

    report = reconcile_stale_running_jobs(
        session_factory=session_factory,
        settings=_make_settings(worker_stale_after_seconds=30),
        trigger="startup",
        now=now,
    )

    assert report == ReconciliationReport(
        scanned_jobs=1,
        stale_jobs=1,
        failed_jobs=1,
        completed_jobs=0,
        duration_ms=report.duration_ms,
    )
    with session_factory() as session:
        job = session.get(Job, job_id)
        assert job is not None
        assert job.status == JobStatus.FAILED
        assert job.error_code == "processing_interrupted"
        assert job.error_message == PROCESSING_INTERRUPTED_ERROR_MESSAGE
        assert job.completed_at == now


def test_reconcile_stale_running_with_result_marks_completed(
    session_factory: sessionmaker[Session],
    tmp_path: Path,
) -> None:
    """A stale running job with persisted result should reconcile to completed."""
    now = datetime(2026, 3, 29, 12, 0, tzinfo=UTC)
    input_path = tmp_path / "completed.wav"
    input_path.write_bytes(b"audio")
    job_id = _persist_job(
        session_factory,
        status=JobStatus.RUNNING,
        stored_path=input_path.as_posix(),
        started_at=now - timedelta(minutes=4),
        last_heartbeat_at=now - timedelta(minutes=2),
        with_result=True,
        error_code="stale_error",
        error_message="stale error message",
    )

    report = reconcile_stale_running_jobs(
        session_factory=session_factory,
        settings=_make_settings(worker_stale_after_seconds=30),
        trigger="startup",
        now=now,
    )

    assert report.stale_jobs == 1
    assert report.completed_jobs == 1
    with session_factory() as session:
        job = session.get(Job, job_id)
        assert job is not None
        assert job.status == JobStatus.COMPLETED
        assert job.error_code is None
        assert job.error_message is None
        assert job.completed_at == now


def test_reconcile_running_with_fresh_heartbeat_is_left_running(
    session_factory: sessionmaker[Session],
    tmp_path: Path,
) -> None:
    """A healthy in-flight running job must not be reconciled."""
    now = datetime(2026, 3, 29, 12, 0, tzinfo=UTC)
    input_path = tmp_path / "fresh.wav"
    input_path.write_bytes(b"audio")
    job_id = _persist_job(
        session_factory,
        status=JobStatus.RUNNING,
        stored_path=input_path.as_posix(),
        started_at=now - timedelta(seconds=20),
        last_heartbeat_at=now - timedelta(seconds=5),
    )

    report = reconcile_stale_running_jobs(
        session_factory=session_factory,
        settings=_make_settings(worker_stale_after_seconds=30),
        trigger="startup",
        now=now,
    )

    assert report.scanned_jobs == 1
    assert report.stale_jobs == 0
    assert report.failed_jobs == 0
    assert report.completed_jobs == 0
    with session_factory() as session:
        job = session.get(Job, job_id)
        assert job is not None
        assert job.status == JobStatus.RUNNING
        assert job.completed_at is None
        assert job.error_code is None


def test_reconcile_legacy_running_uses_started_at_when_heartbeat_missing(
    session_factory: sessionmaker[Session],
    tmp_path: Path,
) -> None:
    """A legacy running job without heartbeat should fall back to started_at."""
    now = datetime(2026, 3, 29, 12, 0, tzinfo=UTC)
    input_path = tmp_path / "legacy.wav"
    input_path.write_bytes(b"audio")
    job_id = _persist_job(
        session_factory,
        status=JobStatus.RUNNING,
        stored_path=input_path.as_posix(),
        started_at=now - timedelta(minutes=5),
        last_heartbeat_at=None,
    )

    report = reconcile_stale_running_jobs(
        session_factory=session_factory,
        settings=_make_settings(worker_stale_after_seconds=30),
        trigger="startup",
        now=now,
    )

    assert report.failed_jobs == 1
    with session_factory() as session:
        job = session.get(Job, job_id)
        assert job is not None
        assert job.status == JobStatus.FAILED
        assert job.error_code == "processing_interrupted"


def test_reconcile_running_without_liveness_timestamps_is_treated_as_stale(
    session_factory: sessionmaker[Session],
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A corrupt running row without any liveness timestamp should reconcile explicitly."""
    now = datetime(2026, 3, 29, 12, 0, tzinfo=UTC)
    input_path = tmp_path / "corrupt.wav"
    input_path.write_bytes(b"audio")
    job_id = _persist_job(
        session_factory,
        status=JobStatus.RUNNING,
        stored_path=input_path.as_posix(),
        started_at=None,
        last_heartbeat_at=None,
    )

    caplog.set_level("INFO", logger="app.worker.service")
    report = reconcile_stale_running_jobs(
        session_factory=session_factory,
        settings=_make_settings(worker_stale_after_seconds=30),
        trigger="startup",
        now=now,
    )

    assert report.failed_jobs == 1
    assert any(
        "anomaly=missing_liveness_timestamps" in record.message
        for record in caplog.records
    )
    with session_factory() as session:
        job = session.get(Job, job_id)
        assert job is not None
        assert job.status == JobStatus.FAILED


def test_reconcile_is_idempotent_and_does_not_rewrite_terminal_fields(
    session_factory: sessionmaker[Session],
    tmp_path: Path,
) -> None:
    """A second reconciliation pass over corrected jobs should be a no-op."""
    first_now = datetime(2026, 3, 29, 12, 0, tzinfo=UTC)
    second_now = first_now + timedelta(minutes=5)
    input_path = tmp_path / "idempotent.wav"
    input_path.write_bytes(b"audio")
    job_id = _persist_job(
        session_factory,
        status=JobStatus.RUNNING,
        stored_path=input_path.as_posix(),
        started_at=first_now - timedelta(minutes=5),
        last_heartbeat_at=first_now - timedelta(minutes=2),
    )

    first_report = reconcile_stale_running_jobs(
        session_factory=session_factory,
        settings=_make_settings(worker_stale_after_seconds=30),
        trigger="startup",
        now=first_now,
    )
    second_report = reconcile_stale_running_jobs(
        session_factory=session_factory,
        settings=_make_settings(worker_stale_after_seconds=30),
        trigger="cadence",
        now=second_now,
    )

    assert first_report.failed_jobs == 1
    assert second_report.stale_jobs == 0
    assert second_report.failed_jobs == 0
    assert second_report.completed_jobs == 0
    with session_factory() as session:
        job = session.get(Job, job_id)
        assert job is not None
        assert job.status == JobStatus.FAILED
        assert job.completed_at == first_now
        assert job.error_code == "processing_interrupted"
        assert job.error_message == PROCESSING_INTERRUPTED_ERROR_MESSAGE


def test_update_job_heartbeat_returns_false_when_job_is_no_longer_running(
    session_factory: sessionmaker[Session],
    tmp_path: Path,
) -> None:
    """Heartbeat refresh should stop once the job is terminal."""
    now = datetime(2026, 3, 29, 12, 0, tzinfo=UTC)
    beat_time = now + timedelta(seconds=10)
    input_path = tmp_path / "terminal.wav"
    input_path.write_bytes(b"audio")
    job_id = _persist_job(
        session_factory,
        status=JobStatus.COMPLETED,
        stored_path=input_path.as_posix(),
        started_at=now - timedelta(minutes=1),
        last_heartbeat_at=now - timedelta(seconds=5),
        completed_at=now,
        with_result=True,
    )

    updated = _update_job_heartbeat(
        session_factory=session_factory,
        job_id=job_id,
        beat_time=beat_time,
    )

    assert updated is False
    with session_factory() as session:
        job = session.get(Job, job_id)
        assert job is not None
        assert job.last_heartbeat_at == now - timedelta(seconds=5)


def test_claim_next_pending_job_initializes_started_and_last_heartbeat(
    session_factory: sessionmaker[Session],
    tmp_path: Path,
) -> None:
    """Claiming a pending job should initialize both active-processing timestamps."""
    input_path = tmp_path / "claim.wav"
    input_path.write_bytes(b"audio")
    job_id = _persist_job(
        session_factory,
        status=JobStatus.PENDING,
        stored_path=input_path.as_posix(),
        started_at=None,
        last_heartbeat_at=None,
    )

    claimed_job_id = claim_next_pending_job(session_factory)

    assert claimed_job_id == job_id
    with session_factory() as session:
        job = session.get(Job, job_id)
        assert job is not None
        assert job.status == JobStatus.RUNNING
        assert job.started_at is not None
        assert job.last_heartbeat_at is not None
        assert job.last_heartbeat_at == job.started_at


def test_run_worker_once_refreshes_heartbeat_during_processing(
    session_factory: sessionmaker[Session],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Longer active processing should trigger at least one background heartbeat refresh."""
    input_path = tmp_path / "heartbeat.wav"
    input_path.write_bytes(b"audio")
    _persist_job(
        session_factory,
        status=JobStatus.PENDING,
        stored_path=input_path.as_posix(),
        started_at=None,
        last_heartbeat_at=None,
    )

    heartbeat_seen = Event()
    heartbeat_updates = {"count": 0}
    original_update_job_heartbeat = worker_service._update_job_heartbeat

    def _wrapped_update_job_heartbeat(
        session_factory: sessionmaker[Session],
        job_id: int,
        beat_time: datetime | None = None,
    ) -> bool:
        updated = original_update_job_heartbeat(
            session_factory=session_factory,
            job_id=job_id,
            beat_time=beat_time,
        )
        if updated:
            heartbeat_updates["count"] += 1
            heartbeat_seen.set()
        return updated

    monkeypatch.setattr(
        worker_service,
        "_update_job_heartbeat",
        _wrapped_update_job_heartbeat,
    )
    monkeypatch.setattr(
        worker_service,
        "inspect_audio_silence",
        lambda *_: SilenceInspectionResult(SilenceClassification.NOT_SILENCE),
    )

    def _transcribe_audio(**_: object) -> AsrTranscriptionResult:
        assert heartbeat_seen.wait(0.5)
        return AsrTranscriptionResult(
            transcript_text="hello world",
            transcript_json={
                "segments": [{"id": 0, "start": 0.0, "end": 1.0, "text": "hello world"}],
                "language": "en",
                "engine": "faster-whisper",
                "model": "small",
            },
            detected_language="en",
            metadata_json={
                "engine": "faster-whisper",
                "model": "small",
                "requested_device": "auto",
                "resolved_device": "cpu",
                "compute_type": "int8",
                "empty_transcript": False,
            },
        )

    monkeypatch.setattr(worker_service, "transcribe_audio", _transcribe_audio)
    monkeypatch.setattr(
        worker_service,
        "diarize_audio",
        lambda **_: DiarizationResult(
            speaker_segments_json=[],
            metadata_json={
                "diarization_enabled": True,
                "diarization_model_id": "pyannote/test-model",
                "diarization_device": "cpu",
                "speaker_count": 0,
            },
        ),
    )

    processed = run_worker_once(
        session_factory=session_factory,
        settings=_make_settings(worker_heartbeat_interval_seconds=0.01),
    )

    assert processed is True
    assert heartbeat_updates["count"] >= 1


def test_run_worker_forever_runs_periodic_reconciliation_on_time_cadence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Steady-state worker mode should trigger reconciliation on its lightweight cadence."""
    recovery_calls: list[dict[str, object]] = []
    recovery_logs: list[dict[str, object]] = []
    perf_counter_values = iter([0.0, 0.6, 0.7, 0.8])
    processed_sequence = iter([True, False])

    monkeypatch.setattr(
        worker_service,
        "perf_counter",
        lambda: next(perf_counter_values),
    )
    monkeypatch.setattr(
        worker_service,
        "reconcile_stale_running_jobs",
        lambda **kwargs: recovery_calls.append(kwargs) or ReconciliationReport(
            scanned_jobs=1,
            stale_jobs=1,
            failed_jobs=1,
            completed_jobs=0,
            duration_ms=1,
        ),
    )
    monkeypatch.setattr(
        worker_service,
        "log_reconciliation_report",
        lambda **kwargs: recovery_logs.append(kwargs),
    )
    monkeypatch.setattr(
        worker_service,
        "run_worker_once",
        lambda **kwargs: next(processed_sequence),
    )
    monkeypatch.setattr(
        worker_service,
        "sleep",
        lambda _: (_ for _ in ()).throw(RuntimeError("stop worker loop")),
    )

    with pytest.raises(RuntimeError, match="stop worker loop"):
        worker_service.run_worker_forever(
            session_factory="fake-session-factory",
            settings=_make_settings(
                worker_heartbeat_interval_seconds=0.1,
                worker_stale_after_seconds=1,
                worker_poll_interval_seconds=0,
                worker_cleanup_every_n_jobs=0,
            ),
        )

    assert recovery_calls == [
        {
            "session_factory": "fake-session-factory",
            "settings": _make_settings(
                worker_heartbeat_interval_seconds=0.1,
                worker_stale_after_seconds=1,
                worker_poll_interval_seconds=0,
                worker_cleanup_every_n_jobs=0,
            ),
            "trigger": "cadence",
        }
    ]
    assert recovery_logs[0]["trigger"] == "cadence"


def test_log_reconciliation_report_includes_summary_and_no_payload_leak(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Recovery logging should stay compact and free of payload fields."""
    caplog.set_level("INFO", logger="app.worker.service")

    log_reconciliation_report(
        logger=worker_service.LOGGER,
        trigger="startup",
        report=ReconciliationReport(
            scanned_jobs=2,
            stale_jobs=1,
            failed_jobs=1,
            completed_jobs=0,
            duration_ms=7,
        ),
    )

    assert any(
        "Running recovery trigger=startup" in record.message
        and "scanned=2" in record.message
        and "failed=1" in record.message
        and "duration_ms=7" in record.message
        for record in caplog.records
    )
    assert "transcript_text" not in caplog.text
    assert "transcript_json" not in caplog.text
    assert "metadata_json" not in caplog.text
