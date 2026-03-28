"""Selected critical-path integration tests for the current backend flows."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
import wave

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select
from sqlalchemy.orm import Session, sessionmaker

from app.api.dependencies import get_session_factory
from app.core.settings import get_settings
from app.db.models import Job, JobResult
from app.main import app
from app.worker.asr import AsrExecutionError, AsrTranscriptionResult
from app.worker.cleanup import run_storage_cleanup
from app.worker.diarization import DiarizationExecutionError, DiarizationResult
from app.worker.service import run_worker_once


def _build_valid_wav_bytes() -> bytes:
    """Return a small valid WAV file generated in memory."""
    buffer = BytesIO()
    with wave.open(buffer, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(16000)
        wav_file.writeframes(b"\x00\x00" * 320)
    return buffer.getvalue()


def _build_asr_result(
    *,
    transcript_text: str,
    segments: list[dict[str, object]],
    language: str | None,
    empty_transcript: bool,
) -> AsrTranscriptionResult:
    """Build one normalized fake ASR result for critical-flow tests."""
    return AsrTranscriptionResult(
        transcript_text=transcript_text,
        transcript_json={
            "segments": segments,
            "language": language,
            "engine": "faster-whisper",
            "model": "small",
        },
        detected_language=language,
        metadata_json={
            "engine": "faster-whisper",
            "model": "small",
            "requested_device": "auto",
            "resolved_device": "cpu",
            "compute_type": "int8",
            "empty_transcript": empty_transcript,
        },
    )


def _build_diarization_result(
    speaker_segments_json: list[dict[str, object]],
) -> DiarizationResult:
    """Build one normalized fake diarization result for critical-flow tests."""
    return DiarizationResult(
        speaker_segments_json=speaker_segments_json,
        metadata_json={
            "diarization_enabled": True,
            "diarization_model_id": "pyannote/test-model",
            "diarization_device": "cpu",
            "speaker_count": len({segment["speaker"] for segment in speaker_segments_json}),
        },
    )


def _make_cleanup_settings(tmp_path: Path) -> SimpleNamespace:
    """Return the minimal cleanup settings shape used by run_storage_cleanup."""
    return SimpleNamespace(
        input_storage_dir=tmp_path / "inputs",
        artifact_storage_dir=tmp_path / "artifacts",
        input_retention_days=7,
        artifact_retention_days=7,
        store_intermediate_artifacts=False,
        worker_cleanup_every_n_jobs=10,
        worker_poll_interval_seconds=0,
    )


@pytest.fixture()
def upload_settings(tmp_path: Path) -> Iterator[None]:
    """Isolate mutable settings and storage paths for critical-flow tests."""
    get_settings.cache_clear()
    get_session_factory.cache_clear()

    settings = get_settings()
    settings.storage_root = tmp_path
    settings.input_storage_dir = tmp_path / "inputs"
    settings.artifact_storage_dir = tmp_path / "artifacts"

    try:
        yield
    finally:
        get_settings.cache_clear()
        get_session_factory.cache_clear()


@pytest.fixture()
def client(upload_settings: None) -> Iterator[TestClient]:
    """Provide an HTTP client with isolated upload settings."""
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture()
def session_factory(upload_settings: None) -> sessionmaker[Session]:
    """Reuse the configured PostgreSQL session factory for these tests."""
    return get_session_factory()


@pytest.fixture(autouse=True)
def clean_jobs_tables(session_factory: sessionmaker[Session]) -> Iterator[None]:
    """Keep job tables isolated before and after each critical-flow test."""
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


def _upload_valid_audio(client: TestClient) -> int:
    """Upload one valid WAV file and return the created job id."""
    response = client.post(
        "/jobs/upload",
        files={"file": ("sample.wav", _build_valid_wav_bytes(), "audio/wav")},
    )

    assert response.status_code == 201
    return int(response.json()["id"])


def test_full_success_flow_exposes_public_result_after_worker_processing(
    client: TestClient,
    session_factory: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A valid upload should reach completed public result retrieval through the worker."""
    monkeypatch.setattr(
        "app.worker.service.transcribe_audio",
        lambda **_: _build_asr_result(
            transcript_text="hello world",
            segments=[
                {"id": 0, "start": 0.0, "end": 0.5, "text": "hello"},
                {"id": 1, "start": 0.5, "end": 1.0, "text": "world"},
            ],
            language="en",
            empty_transcript=False,
        ),
    )
    monkeypatch.setattr(
        "app.worker.service.diarize_audio",
        lambda **_: _build_diarization_result(
            [
                {"speaker": "SPEAKER_00", "start": 0.0, "end": 0.6},
                {"speaker": "SPEAKER_01", "start": 0.6, "end": 1.0},
            ]
        ),
    )

    job_id = _upload_valid_audio(client)

    assert run_worker_once(session_factory=session_factory, settings=get_settings()) is True

    job_response = client.get(f"/jobs/{job_id}")
    result_response = client.get(f"/jobs/{job_id}/result")

    assert job_response.status_code == 200
    assert job_response.json()["status"] == "completed"
    assert result_response.status_code == 200
    assert result_response.json() == {
        "job_id": job_id,
        "transcript_text": "hello world",
        "transcript_json": {
            "segments": [
                {"id": 0, "start": 0.0, "end": 0.5, "text": "hello"},
                {"id": 1, "start": 0.5, "end": 1.0, "text": "world"},
            ],
            "language": "en",
        },
        "speaker_segments_json": [
            {"speaker": "SPEAKER_00", "start": 0.0, "end": 0.6},
            {"speaker": "SPEAKER_01", "start": 0.6, "end": 1.0},
        ],
        "detected_language": "en",
        "empty_transcript": False,
        "diarization_attempted": True,
        "diarization_status": "completed",
    }


def test_degraded_success_flow_keeps_public_result_when_diarization_fails(
    client: TestClient,
    session_factory: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Controlled diarization failure should still leave a public completed result."""
    monkeypatch.setattr(
        "app.worker.service.transcribe_audio",
        lambda **_: _build_asr_result(
            transcript_text="hello world",
            segments=[{"id": 0, "start": 0.0, "end": 1.0, "text": "hello world"}],
            language="en",
            empty_transcript=False,
        ),
    )
    monkeypatch.setattr(
        "app.worker.service.diarize_audio",
        lambda **_: (_ for _ in ()).throw(
            DiarizationExecutionError("controlled diarization failure")
        ),
    )

    job_id = _upload_valid_audio(client)

    assert run_worker_once(session_factory=session_factory, settings=get_settings()) is True

    job_response = client.get(f"/jobs/{job_id}")
    result_response = client.get(f"/jobs/{job_id}/result")

    assert job_response.status_code == 200
    assert job_response.json()["status"] == "completed"
    assert result_response.status_code == 200
    payload = result_response.json()
    assert payload["transcript_text"] == "hello world"
    assert payload["speaker_segments_json"] is None
    assert payload["diarization_status"] == "failed"


def test_terminal_failure_flow_returns_result_409_after_asr_error(
    client: TestClient,
    session_factory: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ASR terminal failure should keep the public result endpoint at 409."""
    monkeypatch.setattr(
        "app.worker.service.transcribe_audio",
        lambda **_: (_ for _ in ()).throw(
            AsrExecutionError("faster-whisper transcription failed")
        ),
    )

    job_id = _upload_valid_audio(client)

    assert run_worker_once(session_factory=session_factory, settings=get_settings()) is True

    job_response = client.get(f"/jobs/{job_id}")
    result_response = client.get(f"/jobs/{job_id}/result")

    assert job_response.status_code == 200
    assert job_response.json()["status"] == "failed"
    assert job_response.json()["error_code"] == "asr_error"
    assert result_response.status_code == 409
    assert result_response.json() == {"detail": "Job result is not available yet."}


def test_valid_empty_transcript_flow_remains_publicly_retrievable(
    client: TestClient,
    session_factory: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A valid empty transcript should still roundtrip through the public result endpoint."""
    monkeypatch.setattr(
        "app.worker.service.transcribe_audio",
        lambda **_: _build_asr_result(
            transcript_text="",
            segments=[],
            language=None,
            empty_transcript=True,
        ),
    )
    monkeypatch.setattr(
        "app.worker.service.diarize_audio",
        lambda **_: _build_diarization_result([]),
    )

    job_id = _upload_valid_audio(client)

    assert run_worker_once(session_factory=session_factory, settings=get_settings()) is True

    result_response = client.get(f"/jobs/{job_id}/result")

    assert result_response.status_code == 200
    assert result_response.json() == {
        "job_id": job_id,
        "transcript_text": "",
        "transcript_json": {
            "segments": [],
            "language": None,
        },
        "speaker_segments_json": [],
        "detected_language": None,
        "empty_transcript": True,
        "diarization_attempted": True,
        "diarization_status": "completed",
    }


def test_public_result_stays_available_after_input_cleanup_removes_expired_file(
    client: TestClient,
    session_factory: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Cleanup should be able to remove the expired local input while result retrieval keeps working."""
    now = datetime(2026, 3, 28, 12, 0, tzinfo=UTC)
    monkeypatch.setattr(
        "app.worker.service.transcribe_audio",
        lambda **_: _build_asr_result(
            transcript_text="cleanup-safe transcript",
            segments=[{"id": 0, "start": 0.0, "end": 1.0, "text": "cleanup-safe transcript"}],
            language="en",
            empty_transcript=False,
        ),
    )
    monkeypatch.setattr(
        "app.worker.service.diarize_audio",
        lambda **_: _build_diarization_result(
            [{"speaker": "SPEAKER_00", "start": 0.0, "end": 1.0}]
        ),
    )

    job_id = _upload_valid_audio(client)

    assert run_worker_once(session_factory=session_factory, settings=get_settings()) is True

    with session_factory() as session:
        job = session.scalar(select(Job).where(Job.id == job_id))
        assert job is not None
        input_path = Path(job.stored_path)
        assert input_path.exists()
        job.completed_at = now - timedelta(days=8)
        job.updated_at = now - timedelta(days=8)
        session.commit()

    cleanup_settings = _make_cleanup_settings(tmp_path)
    report = run_storage_cleanup(
        session_factory=session_factory,
        settings=cleanup_settings,
        now=now,
    )

    assert report.input_files_processed == 1
    assert report.input_files_deleted == 1
    assert not input_path.exists()

    result_response = client.get(f"/jobs/{job_id}/result")

    assert result_response.status_code == 200
    assert result_response.json()["transcript_text"] == "cleanup-safe transcript"
