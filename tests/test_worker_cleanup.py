"""Tests for worker-side TTL cleanup behavior."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import delete, select
from sqlalchemy.orm import Session, sessionmaker

from app.api.dependencies import get_session_factory
from app.db.models import Job, JobResult, JobStatus
import app.worker.service as worker_service
from app.worker.cleanup import _resolve_safe_path, run_storage_cleanup


@pytest.fixture()
def session_factory() -> sessionmaker[Session]:
    """Reuse the configured PostgreSQL session factory for cleanup tests."""
    return get_session_factory()


@pytest.fixture(autouse=True)
def clean_jobs_tables(session_factory: sessionmaker[Session]) -> Iterator[None]:
    """Keep job tables isolated even if a cleanup test fails midway."""
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


def _make_settings(
    tmp_path: Path,
    **overrides: object,
) -> SimpleNamespace:
    """Build a minimal settings object for cleanup tests."""
    values: dict[str, object] = {
        "input_storage_dir": tmp_path / "inputs",
        "artifact_storage_dir": tmp_path / "artifacts",
        "input_retention_days": 7,
        "artifact_retention_days": 7,
        "store_intermediate_artifacts": True,
        "worker_cleanup_every_n_jobs": 10,
        "worker_poll_interval_seconds": 0,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _persist_job(
    session_factory: sessionmaker[Session],
    *,
    status: JobStatus,
    stored_path: str,
    completed_at: datetime | None,
    updated_at: datetime,
    with_result: bool = False,
) -> int:
    """Persist one job row and optionally one linked result row."""
    with session_factory() as session:
        job = Job(
            status=status,
            created_at=updated_at,
            started_at=None,
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
            error_code=None,
            error_message=None,
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


def test_run_storage_cleanup_deletes_expired_completed_input_and_keeps_db_result(
    session_factory: sessionmaker[Session],
    tmp_path: Path,
) -> None:
    """Expired completed-job inputs should be deleted while DB results remain persisted."""
    now = datetime(2026, 3, 28, 12, 0, tzinfo=UTC)
    settings = _make_settings(tmp_path)
    settings.input_storage_dir.mkdir(parents=True)
    input_path = settings.input_storage_dir / "expired.wav"
    input_path.write_bytes(b"audio")

    job_id = _persist_job(
        session_factory,
        status=JobStatus.COMPLETED,
        stored_path=input_path.as_posix(),
        completed_at=now - timedelta(days=8),
        updated_at=now - timedelta(days=8),
        with_result=True,
    )

    report = run_storage_cleanup(
        session_factory=session_factory,
        settings=settings,
        now=now,
    )

    assert report.input_files_processed == 1
    assert report.input_files_deleted == 1
    assert not input_path.exists()
    with session_factory() as session:
        assert session.scalar(
            select(JobResult.id).where(JobResult.job_id == job_id)
        ) is not None


def test_run_storage_cleanup_deletes_expired_failed_input(
    session_factory: sessionmaker[Session],
    tmp_path: Path,
) -> None:
    """Expired failed-job inputs should also be deleted."""
    now = datetime(2026, 3, 28, 12, 0, tzinfo=UTC)
    settings = _make_settings(tmp_path)
    settings.input_storage_dir.mkdir(parents=True)
    input_path = settings.input_storage_dir / "failed.wav"
    input_path.write_bytes(b"audio")

    report_before = input_path.exists()
    assert report_before is True

    _persist_job(
        session_factory,
        status=JobStatus.FAILED,
        stored_path=input_path.as_posix(),
        completed_at=now - timedelta(days=8),
        updated_at=now - timedelta(days=8),
    )

    report = run_storage_cleanup(
        session_factory=session_factory,
        settings=settings,
        now=now,
    )

    assert report.input_files_processed == 1
    assert report.input_files_deleted == 1
    assert not input_path.exists()


def test_run_storage_cleanup_preserves_non_expired_input(
    session_factory: sessionmaker[Session],
    tmp_path: Path,
) -> None:
    """Terminal inputs newer than the cutoff should be preserved."""
    now = datetime(2026, 3, 28, 12, 0, tzinfo=UTC)
    settings = _make_settings(tmp_path)
    settings.input_storage_dir.mkdir(parents=True)
    input_path = settings.input_storage_dir / "fresh.wav"
    input_path.write_bytes(b"audio")

    _persist_job(
        session_factory,
        status=JobStatus.COMPLETED,
        stored_path=input_path.as_posix(),
        completed_at=now - timedelta(days=1),
        updated_at=now - timedelta(days=1),
    )

    report = run_storage_cleanup(
        session_factory=session_factory,
        settings=settings,
        now=now,
    )

    assert report.input_files_processed == 1
    assert report.input_files_deleted == 0
    assert input_path.exists()


def test_run_storage_cleanup_retention_zero_makes_terminal_input_immediately_eligible(
    session_factory: sessionmaker[Session],
    tmp_path: Path,
) -> None:
    """A zero-day retention should make already-terminal inputs immediately eligible."""
    now = datetime(2026, 3, 28, 12, 0, tzinfo=UTC)
    settings = _make_settings(tmp_path, input_retention_days=0)
    settings.input_storage_dir.mkdir(parents=True)
    input_path = settings.input_storage_dir / "immediate.wav"
    input_path.write_bytes(b"audio")

    _persist_job(
        session_factory,
        status=JobStatus.COMPLETED,
        stored_path=input_path.as_posix(),
        completed_at=now - timedelta(seconds=1),
        updated_at=now - timedelta(seconds=1),
    )

    report = run_storage_cleanup(
        session_factory=session_factory,
        settings=settings,
        now=now,
    )

    assert report.input_files_deleted == 1
    assert not input_path.exists()


def test_run_storage_cleanup_negative_input_retention_disables_input_cleanup_with_warning(
    session_factory: sessionmaker[Session],
    tmp_path: Path,
) -> None:
    """Negative input retention should disable the category without deleting files."""
    now = datetime(2026, 3, 28, 12, 0, tzinfo=UTC)
    settings = _make_settings(tmp_path, input_retention_days=-1)
    settings.input_storage_dir.mkdir(parents=True)
    input_path = settings.input_storage_dir / "disabled.wav"
    input_path.write_bytes(b"audio")

    _persist_job(
        session_factory,
        status=JobStatus.COMPLETED,
        stored_path=input_path.as_posix(),
        completed_at=now - timedelta(days=30),
        updated_at=now - timedelta(days=30),
    )

    report = run_storage_cleanup(
        session_factory=session_factory,
        settings=settings,
        now=now,
    )

    assert report.input_files_processed == 0
    assert report.input_files_deleted == 0
    assert input_path.exists()
    assert any("Input cleanup is disabled" in warning for warning in report.warnings)


def test_run_storage_cleanup_preserves_shared_input_when_any_reference_is_active(
    session_factory: sessionmaker[Session],
    tmp_path: Path,
) -> None:
    """Shared input files must survive if any referencing job is still active."""
    now = datetime(2026, 3, 28, 12, 0, tzinfo=UTC)
    settings = _make_settings(tmp_path)
    settings.input_storage_dir.mkdir(parents=True)
    input_path = settings.input_storage_dir / "shared.wav"
    input_path.write_bytes(b"audio")

    _persist_job(
        session_factory,
        status=JobStatus.COMPLETED,
        stored_path=input_path.as_posix(),
        completed_at=now - timedelta(days=30),
        updated_at=now - timedelta(days=30),
    )
    _persist_job(
        session_factory,
        status=JobStatus.RUNNING,
        stored_path=input_path.as_posix(),
        completed_at=None,
        updated_at=now - timedelta(days=30),
    )

    report = run_storage_cleanup(
        session_factory=session_factory,
        settings=settings,
        now=now,
    )

    assert report.input_files_processed == 1
    assert report.input_files_deleted == 0
    assert input_path.exists()


def test_run_storage_cleanup_deduplicates_shared_input_by_physical_path(
    session_factory: sessionmaker[Session],
    tmp_path: Path,
) -> None:
    """One physical input path should be evaluated at most once per cleanup pass."""
    now = datetime(2026, 3, 28, 12, 0, tzinfo=UTC)
    settings = _make_settings(tmp_path)
    settings.input_storage_dir.mkdir(parents=True)
    input_path = settings.input_storage_dir / "dedup.wav"
    input_path.write_bytes(b"audio")

    _persist_job(
        session_factory,
        status=JobStatus.COMPLETED,
        stored_path=input_path.as_posix(),
        completed_at=now - timedelta(days=30),
        updated_at=now - timedelta(days=30),
    )
    _persist_job(
        session_factory,
        status=JobStatus.FAILED,
        stored_path=input_path.as_posix(),
        completed_at=now - timedelta(days=30),
        updated_at=now - timedelta(days=30),
    )

    report = run_storage_cleanup(
        session_factory=session_factory,
        settings=settings,
        now=now,
    )

    assert report.input_files_processed == 1
    assert report.input_files_deleted == 1
    assert not input_path.exists()


def test_run_storage_cleanup_is_noop_when_input_root_is_missing(
    session_factory: sessionmaker[Session],
    tmp_path: Path,
) -> None:
    """Missing input storage roots should be treated as a safe no-op."""
    now = datetime(2026, 3, 28, 12, 0, tzinfo=UTC)
    settings = _make_settings(tmp_path)

    report = run_storage_cleanup(
        session_factory=session_factory,
        settings=settings,
        now=now,
    )

    assert report.input_files_processed == 0
    assert report.input_files_deleted == 0
    assert report.warnings == ()


@pytest.mark.parametrize("stored_path", ["", "   "])
def test_run_storage_cleanup_skips_blank_stored_path_safely(
    session_factory: sessionmaker[Session],
    tmp_path: Path,
    stored_path: str,
) -> None:
    """Rows with blank stored_path should be ignored safely and silently."""
    now = datetime(2026, 3, 28, 12, 0, tzinfo=UTC)
    settings = _make_settings(tmp_path)
    settings.input_storage_dir.mkdir(parents=True)

    _persist_job(
        session_factory,
        status=JobStatus.COMPLETED,
        stored_path=stored_path,
        completed_at=now - timedelta(days=30),
        updated_at=now - timedelta(days=30),
    )

    report = run_storage_cleanup(
        session_factory=session_factory,
        settings=settings,
        now=now,
    )

    assert report.input_files_processed == 0
    assert report.input_files_deleted == 0
    assert report.warnings == ()


def test_resolve_safe_path_skips_missing_input_path_silently(tmp_path: Path) -> None:
    """Defensive None stored_path handling should remain a benign silent skip."""
    warnings: list[str] = []
    input_root = tmp_path / "inputs"
    input_root.mkdir(parents=True)

    safe_path = _resolve_safe_path(
        raw_path=None,
        root=input_root,
        category="input",
        warnings=warnings,
    )

    assert safe_path is None
    assert warnings == []


def test_run_storage_cleanup_none_input_retention_disables_input_cleanup_with_warning(
    session_factory: sessionmaker[Session],
    tmp_path: Path,
) -> None:
    """None input retention should disable the category without deleting files."""
    now = datetime(2026, 3, 28, 12, 0, tzinfo=UTC)
    settings = _make_settings(tmp_path, input_retention_days=None)
    settings.input_storage_dir.mkdir(parents=True)
    input_path = settings.input_storage_dir / "disabled-none.wav"
    input_path.write_bytes(b"audio")

    _persist_job(
        session_factory,
        status=JobStatus.COMPLETED,
        stored_path=input_path.as_posix(),
        completed_at=now - timedelta(days=30),
        updated_at=now - timedelta(days=30),
    )

    report = run_storage_cleanup(
        session_factory=session_factory,
        settings=settings,
        now=now,
    )

    assert report.input_files_processed == 0
    assert report.input_files_deleted == 0
    assert input_path.exists()
    assert any("Input cleanup is disabled" in warning for warning in report.warnings)


def test_run_storage_cleanup_treats_missing_input_file_as_benign(
    session_factory: sessionmaker[Session],
    tmp_path: Path,
) -> None:
    """Already-absent input files should not break cleanup."""
    now = datetime(2026, 3, 28, 12, 0, tzinfo=UTC)
    settings = _make_settings(tmp_path)
    settings.input_storage_dir.mkdir(parents=True)
    input_path = settings.input_storage_dir / "missing.wav"

    _persist_job(
        session_factory,
        status=JobStatus.COMPLETED,
        stored_path=input_path.as_posix(),
        completed_at=now - timedelta(days=30),
        updated_at=now - timedelta(days=30),
    )

    report = run_storage_cleanup(
        session_factory=session_factory,
        settings=settings,
        now=now,
    )

    assert report.input_files_processed == 1
    assert report.input_files_deleted == 0
    assert report.warnings == ()


def test_run_storage_cleanup_rejects_input_outside_configured_root(
    session_factory: sessionmaker[Session],
    tmp_path: Path,
) -> None:
    """Unsafe input paths outside the configured root must not be deleted."""
    now = datetime(2026, 3, 28, 12, 0, tzinfo=UTC)
    settings = _make_settings(tmp_path)
    settings.input_storage_dir.mkdir(parents=True)
    outside_path = tmp_path / "outside.wav"
    outside_path.write_bytes(b"audio")

    _persist_job(
        session_factory,
        status=JobStatus.COMPLETED,
        stored_path=outside_path.as_posix(),
        completed_at=now - timedelta(days=30),
        updated_at=now - timedelta(days=30),
    )

    report = run_storage_cleanup(
        session_factory=session_factory,
        settings=settings,
        now=now,
    )

    assert report.input_files_processed == 0
    assert report.input_files_deleted == 0
    assert outside_path.exists()
    assert any("outside configured root" in warning for warning in report.warnings)


def test_run_storage_cleanup_deletes_expired_artifact_and_prunes_empty_directory(
    session_factory: sessionmaker[Session],
    tmp_path: Path,
) -> None:
    """Expired artifact files should be deleted and empty directories can be pruned."""
    now = datetime(2026, 3, 28, 12, 0, tzinfo=UTC)
    settings = _make_settings(tmp_path)
    nested_dir = settings.artifact_storage_dir / "nested"
    nested_dir.mkdir(parents=True)
    artifact_path = nested_dir / "old.bin"
    artifact_path.write_bytes(b"artifact")
    expired_mtime = (now - timedelta(days=30)).timestamp()
    os.utime(artifact_path, (expired_mtime, expired_mtime))

    report = run_storage_cleanup(
        session_factory=session_factory,
        settings=settings,
        now=now,
    )

    assert report.artifact_files_processed == 1
    assert report.artifact_files_deleted == 1
    assert report.artifact_dirs_deleted == 1
    assert not artifact_path.exists()
    assert not nested_dir.exists()


def test_run_storage_cleanup_preserves_nonexpired_artifact(
    session_factory: sessionmaker[Session],
    tmp_path: Path,
) -> None:
    """Artifacts newer than the cutoff should remain on disk."""
    now = datetime(2026, 3, 28, 12, 0, tzinfo=UTC)
    settings = _make_settings(tmp_path)
    settings.artifact_storage_dir.mkdir(parents=True)
    artifact_path = settings.artifact_storage_dir / "fresh.bin"
    artifact_path.write_bytes(b"artifact")
    fresh_mtime = (now - timedelta(days=1)).timestamp()
    os.utime(artifact_path, (fresh_mtime, fresh_mtime))

    report = run_storage_cleanup(
        session_factory=session_factory,
        settings=settings,
        now=now,
    )

    assert report.artifact_files_processed == 1
    assert report.artifact_files_deleted == 0
    assert artifact_path.exists()


def test_run_storage_cleanup_negative_artifact_retention_disables_artifact_cleanup_with_warning(
    session_factory: sessionmaker[Session],
    tmp_path: Path,
) -> None:
    """Negative artifact retention should disable artifact cleanup."""
    now = datetime(2026, 3, 28, 12, 0, tzinfo=UTC)
    settings = _make_settings(tmp_path, artifact_retention_days=-1)
    settings.artifact_storage_dir.mkdir(parents=True)
    artifact_path = settings.artifact_storage_dir / "disabled.bin"
    artifact_path.write_bytes(b"artifact")
    expired_mtime = (now - timedelta(days=30)).timestamp()
    os.utime(artifact_path, (expired_mtime, expired_mtime))

    report = run_storage_cleanup(
        session_factory=session_factory,
        settings=settings,
        now=now,
    )

    assert report.artifact_files_processed == 0
    assert report.artifact_files_deleted == 0
    assert artifact_path.exists()
    assert any("Artifact cleanup is disabled" in warning for warning in report.warnings)


def test_run_storage_cleanup_none_artifact_retention_disables_artifact_cleanup_with_warning(
    session_factory: sessionmaker[Session],
    tmp_path: Path,
) -> None:
    """None artifact retention should disable artifact cleanup."""
    now = datetime(2026, 3, 28, 12, 0, tzinfo=UTC)
    settings = _make_settings(tmp_path, artifact_retention_days=None)
    settings.artifact_storage_dir.mkdir(parents=True)
    artifact_path = settings.artifact_storage_dir / "disabled-none.bin"
    artifact_path.write_bytes(b"artifact")
    expired_mtime = (now - timedelta(days=30)).timestamp()
    os.utime(artifact_path, (expired_mtime, expired_mtime))

    report = run_storage_cleanup(
        session_factory=session_factory,
        settings=settings,
        now=now,
    )

    assert report.artifact_files_processed == 0
    assert report.artifact_files_deleted == 0
    assert artifact_path.exists()
    assert any("Artifact cleanup is disabled" in warning for warning in report.warnings)


def test_resolve_safe_path_rejects_artifact_path_outside_root(tmp_path: Path) -> None:
    """Artifact path-safety should reject paths that escape the configured root."""
    warnings: list[str] = []
    artifact_root = tmp_path / "artifacts"
    artifact_root.mkdir(parents=True)
    outside_path = tmp_path / "outside.bin"
    outside_path.write_bytes(b"artifact")

    safe_path = _resolve_safe_path(
        raw_path=outside_path,
        root=artifact_root,
        category="artifact",
        warnings=warnings,
    )

    assert safe_path is None
    assert outside_path.exists()
    assert any("outside configured root" in warning for warning in warnings)


def test_run_worker_forever_runs_periodic_cleanup_on_configured_cadence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Long-running mode should trigger periodic cleanup only on the configured cadence."""
    processed_sequence = iter([True, True, False])
    cleanup_calls: list[dict[str, object]] = []
    cleanup_logs: list[dict[str, object]] = []

    monkeypatch.setattr(
        worker_service,
        "run_worker_once",
        lambda **kwargs: next(processed_sequence),
    )
    monkeypatch.setattr(
        worker_service,
        "run_storage_cleanup",
        lambda **kwargs: cleanup_calls.append(kwargs) or "cleanup-report",
    )
    monkeypatch.setattr(
        worker_service,
        "log_storage_cleanup_report",
        lambda **kwargs: cleanup_logs.append(kwargs),
    )
    monkeypatch.setattr(
        worker_service,
        "sleep",
        lambda _: (_ for _ in ()).throw(RuntimeError("stop worker loop")),
    )
    settings = SimpleNamespace(
        worker_cleanup_every_n_jobs=2,
        worker_poll_interval_seconds=0,
    )

    with pytest.raises(RuntimeError, match="stop worker loop"):
        worker_service.run_worker_forever(
            session_factory="fake-session-factory",
            settings=settings,
        )

    assert cleanup_calls == [
        {
            "session_factory": "fake-session-factory",
            "settings": settings,
        }
    ]
    assert cleanup_logs[0]["trigger"] == "cadence"


@pytest.mark.parametrize("cleanup_every_n_jobs", [0, -1, None])
def test_run_worker_forever_disables_periodic_cleanup_for_nonpositive_or_missing_cadence(
    monkeypatch: pytest.MonkeyPatch,
    cleanup_every_n_jobs: int | None,
) -> None:
    """Nonpositive or missing cleanup cadence should disable periodic cleanup without error."""
    processed_sequence = iter([True, True, False])
    cleanup_calls: list[dict[str, object]] = []

    monkeypatch.setattr(
        worker_service,
        "run_worker_once",
        lambda **kwargs: next(processed_sequence),
    )
    monkeypatch.setattr(
        worker_service,
        "run_storage_cleanup",
        lambda **kwargs: cleanup_calls.append(kwargs) or "cleanup-report",
    )
    monkeypatch.setattr(
        worker_service,
        "log_storage_cleanup_report",
        lambda **kwargs: None,
    )
    monkeypatch.setattr(
        worker_service,
        "sleep",
        lambda _: (_ for _ in ()).throw(RuntimeError("stop worker loop")),
    )

    with pytest.raises(RuntimeError, match="stop worker loop"):
        worker_service.run_worker_forever(
            session_factory="fake-session-factory",
            settings=SimpleNamespace(
                worker_cleanup_every_n_jobs=cleanup_every_n_jobs,
                worker_poll_interval_seconds=0,
            ),
        )

    assert cleanup_calls == []
