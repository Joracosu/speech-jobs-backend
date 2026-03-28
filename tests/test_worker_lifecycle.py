"""Tests for the dedicated worker lifecycle behavior."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import delete, select
from sqlalchemy.orm import Session, sessionmaker

from app.db.models import Job, JobResult, JobStatus
from app.worker.asr import AsrExecutionError, AsrTranscriptionResult
from app.worker.diarization import DiarizationExecutionError, DiarizationResult
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


def test_run_worker_once_completes_pending_job_and_persists_asr_result(
    session_factory: sessionmaker[Session],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Worker should persist a completed ASR+diarization result on success."""
    input_path = tmp_path / "input.wav"
    input_path.write_bytes(b"real-audio-bytes")

    monkeypatch.setattr(
        "app.worker.service.transcribe_audio",
        lambda **_: AsrTranscriptionResult(
            transcript_text="hello world",
            transcript_json={
                "segments": [
                    {"id": 0, "start": 0.0, "end": 0.5, "text": "hello"},
                    {"id": 1, "start": 0.5, "end": 1.0, "text": "world"},
                ],
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
        ),
    )
    monkeypatch.setattr(
        "app.worker.service.diarize_audio",
        lambda **_: DiarizationResult(
            speaker_segments_json=[
                {"speaker": "speaker_0", "start": 0.0, "end": 0.6},
                {"speaker": "speaker_1", "start": 0.6, "end": 1.0},
            ],
            metadata_json={
                "diarization_enabled": True,
                "diarization_model_id": "pyannote/test-model",
                "diarization_device": "cpu",
                "speaker_count": 2,
            },
        ),
    )

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
        assert persisted_result.transcript_text == "hello world"
        assert persisted_result.transcript_json == {
            "segments": [
                {"id": 0, "start": 0.0, "end": 0.5, "text": "hello"},
                {"id": 1, "start": 0.5, "end": 1.0, "text": "world"},
            ],
            "language": "en",
            "engine": "faster-whisper",
            "model": "small",
        }
        assert persisted_result.speaker_segments_json == [
            {"speaker": "speaker_0", "start": 0.0, "end": 0.6},
            {"speaker": "speaker_1", "start": 0.6, "end": 1.0},
        ]
        assert persisted_result.detected_language == "en"
        assert persisted_result.metadata_json == {
            "mode": "asr+diarization",
            "engine": "faster-whisper",
            "profile": "balanced",
            "model": "small",
            "requested_device": "auto",
            "resolved_device": "cpu",
            "compute_type": "int8",
            "worker_id": "local-worker-1",
            "empty_transcript": False,
            "diarization_attempted": True,
            "diarization_status": "completed",
            "diarization_enabled": True,
            "diarization_model_id": "pyannote/test-model",
            "diarization_device": "cpu",
            "speaker_count": 2,
        }


def test_run_worker_once_persists_empty_speaker_segments_on_success(
    session_factory: sessionmaker[Session],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Successful diarization with zero valid segments should persist an empty list."""
    input_path = tmp_path / "input.wav"
    input_path.write_bytes(b"real-audio-bytes")

    monkeypatch.setattr(
        "app.worker.service.transcribe_audio",
        lambda **_: AsrTranscriptionResult(
            transcript_text="hello",
            transcript_json={
                "segments": [{"id": 0, "start": 0.0, "end": 0.5, "text": "hello"}],
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
        ),
    )
    monkeypatch.setattr(
        "app.worker.service.diarize_audio",
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

    with session_factory() as session:
        job = Job(
            status=JobStatus.PENDING,
            original_filename="input.wav",
            stored_path=input_path.as_posix(),
            input_sha256="bb" * 32,
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

    assert run_worker_once(session_factory=session_factory) is True

    with session_factory() as session:
        persisted_job = session.get(Job, job_id)
        assert persisted_job is not None
        assert persisted_job.status == JobStatus.COMPLETED

        persisted_result = session.scalar(
            select(JobResult).where(JobResult.job_id == job_id)
        )
        assert persisted_result is not None
        assert persisted_result.speaker_segments_json == []
        assert persisted_result.metadata_json["diarization_attempted"] is True
        assert persisted_result.metadata_json["diarization_status"] == "completed"
        assert persisted_result.metadata_json["speaker_count"] == 0


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


def test_run_worker_once_marks_job_failed_on_asr_error(
    session_factory: sessionmaker[Session],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Worker should persist a clean failed state when ASR execution fails."""
    input_path = tmp_path / "input.wav"
    input_path.write_bytes(b"real-audio-bytes")

    monkeypatch.setattr(
        "app.worker.service.transcribe_audio",
        lambda **_: (_ for _ in ()).throw(
            AsrExecutionError("faster-whisper transcription failed")
        ),
    )

    with session_factory() as session:
        job = Job(
            status=JobStatus.PENDING,
            original_filename="input.wav",
            stored_path=input_path.as_posix(),
            input_sha256="d" * 64,
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
        assert persisted_job.status == JobStatus.FAILED
        assert persisted_job.error_code == "asr_error"
        assert persisted_job.error_message == "faster-whisper transcription failed"
        assert session.scalar(
            select(JobResult).where(JobResult.job_id == job_id)
        ) is None


def test_run_worker_once_completes_job_with_degraded_result_on_diarization_error(
    session_factory: sessionmaker[Session],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Worker should preserve a valid ASR result when diarization fails in a controlled way."""
    input_path = tmp_path / "input.wav"
    input_path.write_bytes(b"real-audio-bytes")

    monkeypatch.setattr(
        "app.worker.service.transcribe_audio",
        lambda **_: AsrTranscriptionResult(
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
        ),
    )
    monkeypatch.setattr(
        "app.worker.service.diarize_audio",
        lambda **_: (_ for _ in ()).throw(
            DiarizationExecutionError("pyannote.audio diarization failed")
        ),
    )

    with session_factory() as session:
        job = Job(
            status=JobStatus.PENDING,
            original_filename="input.wav",
            stored_path=input_path.as_posix(),
            input_sha256="dd" * 32,
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
        assert persisted_job.error_code is None
        assert persisted_job.error_message is None

        persisted_result = session.scalar(
            select(JobResult).where(JobResult.job_id == job_id)
        )
        assert persisted_result is not None
        assert persisted_result.transcript_text == "hello world"
        assert persisted_result.speaker_segments_json is None
        assert persisted_result.metadata_json == {
            "mode": "asr",
            "engine": "faster-whisper",
            "profile": "balanced",
            "model": "small",
            "requested_device": "auto",
            "resolved_device": "cpu",
            "compute_type": "int8",
            "worker_id": "local-worker-1",
            "empty_transcript": False,
            "diarization_attempted": True,
            "diarization_status": "failed",
            "diarization_error_code": "diarization_error",
            "diarization_error_message": "pyannote.audio diarization failed",
        }


def test_run_worker_once_preserves_empty_valid_asr_result_when_diarization_fails(
    session_factory: sessionmaker[Session],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A valid empty ASR result should still survive controlled diarization degradation."""
    input_path = tmp_path / "input.wav"
    input_path.write_bytes(b"real-audio-bytes")

    monkeypatch.setattr(
        "app.worker.service.transcribe_audio",
        lambda **_: AsrTranscriptionResult(
            transcript_text="",
            transcript_json={
                "segments": [],
                "language": None,
                "engine": "faster-whisper",
                "model": "small",
            },
            detected_language=None,
            metadata_json={
                "engine": "faster-whisper",
                "model": "small",
                "requested_device": "auto",
                "resolved_device": "cpu",
                "compute_type": "int8",
                "empty_transcript": True,
            },
        ),
    )
    diagnostic_message = (
        "Diarization dependency 'torch' is unavailable: No module named 'torch'. "
        "Run 'python -m app.worker.main --preflight --device cuda' to verify worker runtime readiness."
    )
    monkeypatch.setattr(
        "app.worker.service.diarize_audio",
        lambda **_: (_ for _ in ()).throw(
            DiarizationExecutionError(diagnostic_message)
        ),
    )

    with session_factory() as session:
        job = Job(
            status=JobStatus.PENDING,
            original_filename="input.wav",
            stored_path=input_path.as_posix(),
            input_sha256="de" * 32,
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

    assert run_worker_once(session_factory=session_factory) is True

    with session_factory() as session:
        persisted_job = session.get(Job, job_id)
        assert persisted_job is not None
        assert persisted_job.status == JobStatus.COMPLETED
        assert persisted_job.error_code is None
        assert persisted_job.error_message is None

        persisted_result = session.scalar(
            select(JobResult).where(JobResult.job_id == job_id)
        )
        assert persisted_result is not None
        assert persisted_result.transcript_text == ""
        assert persisted_result.transcript_json["segments"] == []
        assert persisted_result.detected_language is None
        assert persisted_result.speaker_segments_json is None
        assert persisted_result.metadata_json == {
            "mode": "asr",
            "engine": "faster-whisper",
            "profile": "balanced",
            "model": "small",
            "requested_device": "auto",
            "resolved_device": "cpu",
            "compute_type": "int8",
            "worker_id": "local-worker-1",
            "empty_transcript": True,
            "diarization_attempted": True,
            "diarization_status": "failed",
            "diarization_error_code": "diarization_error",
            "diarization_error_message": diagnostic_message,
        }


def test_run_worker_once_marks_job_failed_when_completed_result_persistence_fails(
    session_factory: sessionmaker[Session],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Persistence failures must remain terminal even after a valid ASR result exists."""
    input_path = tmp_path / "input.wav"
    input_path.write_bytes(b"real-audio-bytes")

    monkeypatch.setattr(
        "app.worker.service.transcribe_audio",
        lambda **_: AsrTranscriptionResult(
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
        ),
    )
    monkeypatch.setattr(
        "app.worker.service.diarize_audio",
        lambda **_: (_ for _ in ()).throw(
            DiarizationExecutionError("pyannote.audio diarization failed")
        ),
    )
    monkeypatch.setattr(
        "app.worker.service._persist_completed_job",
        lambda **_: (_ for _ in ()).throw(RuntimeError("db write boom")),
    )

    with session_factory() as session:
        job = Job(
            status=JobStatus.PENDING,
            original_filename="input.wav",
            stored_path=input_path.as_posix(),
            input_sha256="df" * 32,
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

    assert run_worker_once(session_factory=session_factory) is True

    with session_factory() as session:
        persisted_job = session.get(Job, job_id)
        assert persisted_job is not None
        assert persisted_job.status == JobStatus.FAILED
        assert persisted_job.error_code == "processing_error"
        assert persisted_job.error_message == (
            "Failed to persist processing result: db write boom"
        )
        assert session.scalar(
            select(JobResult).where(JobResult.job_id == job_id)
        ) is None


def test_run_worker_once_fails_when_job_already_has_a_result(
    session_factory: sessionmaker[Session],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Worker should not overwrite or duplicate an already persisted result."""
    input_path = tmp_path / "input.wav"
    input_path.write_bytes(b"real-audio-bytes")

    def _unexpected_transcribe(**_: object) -> AsrTranscriptionResult:
        raise AssertionError("transcribe_audio should not be called")

    monkeypatch.setattr("app.worker.service.transcribe_audio", _unexpected_transcribe)

    with session_factory() as session:
        job = Job(
            status=JobStatus.PENDING,
            original_filename="input.wav",
            stored_path=input_path.as_posix(),
            input_sha256="e" * 64,
            file_size_bytes=input_path.stat().st_size,
            media_duration_seconds=None,
            device_used="auto",
            profile_selected="balanced",
            config_snapshot={"profile": "balanced", "device_preference": "auto"},
            error_code=None,
            error_message=None,
        )
        job.result = JobResult(
            transcript_text="already there",
            transcript_json={
                "segments": [],
                "language": None,
                "engine": "faster-whisper",
                "model": "small",
            },
            speaker_segments_json=None,
            detected_language=None,
            metadata_json={"mode": "asr"},
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
        assert persisted_job.error_code == "processing_error"
        assert persisted_job.error_message == "Job already has a persisted result."

        persisted_results = list(
            session.scalars(select(JobResult).where(JobResult.job_id == job_id)).all()
        )
        assert len(persisted_results) == 1
        assert persisted_results[0].transcript_text == "already there"
