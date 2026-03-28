"""Tests for the read-only jobs API."""

from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete
from sqlalchemy.orm import Session, sessionmaker

from app.api.dependencies import get_session_factory
from app.api.schemas.jobs import JobRead
from app.db.models import Job, JobResult, JobStatus
from app.main import app

PUBLIC_RESULT_KEYS = {
    "job_id",
    "transcript_text",
    "transcript_json",
    "speaker_segments_json",
    "detected_language",
    "empty_transcript",
    "diarization_attempted",
    "diarization_status",
}
FORBIDDEN_PUBLIC_KEYS = {
    "metadata_json",
    "diarization_error_code",
    "diarization_error_message",
    "worker_id",
    "requested_device",
    "resolved_device",
    "compute_type",
    "engine",
    "model",
}


@pytest.fixture(scope="module")
def client() -> Iterator[TestClient]:
    """Provide a shared HTTP client for the jobs API tests."""
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture(scope="module")
def session_factory() -> sessionmaker[Session]:
    """Reuse the process-wide session factory used by the API layer."""
    return get_session_factory()


@pytest.fixture(autouse=True)
def clean_jobs_tables(session_factory: sessionmaker[Session]) -> Iterator[None]:
    """Keep jobs tables isolated even if a test fails midway."""
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


def _create_job(
    session_factory: sessionmaker[Session],
    *,
    status: JobStatus,
    result_data: dict[str, Any] | None = None,
) -> int:
    """Persist one job and optionally one linked result row."""
    with session_factory() as session:
        job = Job(
            status=status,
            original_filename="meeting.wav",
            stored_path="inputs/meeting.wav",
            input_sha256="a" * 64,
            file_size_bytes=2048,
            media_duration_seconds=12.5,
            device_used="cpu",
            profile_selected="balanced",
            config_snapshot={"profile": "balanced", "device_preference": "cpu"},
            error_code=None,
            error_message=None,
        )
        if result_data is not None:
            job.result = JobResult(**result_data)
        session.add(job)
        session.commit()
        session.refresh(job)
        return job.id


def _collect_keys(value: object) -> set[str]:
    """Return all dictionary keys found recursively in a JSON-like payload."""
    keys: set[str] = set()
    if isinstance(value, dict):
        for key, child in value.items():
            keys.add(key)
            keys.update(_collect_keys(child))
    elif isinstance(value, list):
        for child in value:
            keys.update(_collect_keys(child))
    return keys


def _assert_public_result_contract(payload: dict[str, Any]) -> None:
    """Assert that one public result payload only exposes curated fields."""
    assert set(payload) == PUBLIC_RESULT_KEYS
    assert isinstance(payload["transcript_json"], dict)
    assert set(payload["transcript_json"]) == {"segments", "language"}
    assert isinstance(payload["transcript_json"]["segments"], list)
    assert payload["transcript_json"]["language"] == payload["detected_language"]
    assert payload["diarization_status"] in {"completed", "failed", None}

    exposed_keys = _collect_keys(payload)
    assert FORBIDDEN_PUBLIC_KEYS.isdisjoint(exposed_keys)


def test_list_jobs_returns_empty_collection(client: TestClient) -> None:
    """Jobs list should return HTTP 200 with an empty array when no rows exist."""
    response = client.get("/jobs")

    assert response.status_code == 200
    assert response.json() == []


def test_get_job_returns_404_when_missing(client: TestClient) -> None:
    """Single-job endpoint should return HTTP 404 when the row does not exist."""
    response = client.get("/jobs/999999")

    assert response.status_code == 404
    assert response.json() == {"detail": "Job not found"}


def test_get_job_returns_public_payload_for_existing_row(
    client: TestClient,
    session_factory: sessionmaker[Session],
) -> None:
    """Single-job endpoint should serialize only the public job fields."""
    with session_factory() as session:
        job = Job(
            status=JobStatus.COMPLETED,
            original_filename="meeting.wav",
            stored_path="inputs/meeting.wav",
            input_sha256="a" * 64,
            file_size_bytes=2048,
            media_duration_seconds=12.5,
            device_used="cpu",
            profile_selected="balanced",
            config_snapshot={"profile": "balanced"},
            error_code=None,
            error_message=None,
        )
        session.add(job)
        session.commit()
        session.refresh(job)
        expected_payload = JobRead.model_validate(job).model_dump(mode="json")
        job_id = job.id

    response = client.get(f"/jobs/{job_id}")

    assert response.status_code == 200
    assert response.json() == expected_payload
    assert "stored_path" not in response.json()
    assert "input_sha256" not in response.json()
    assert "config_snapshot" not in response.json()


def test_get_job_result_returns_404_when_job_is_missing(client: TestClient) -> None:
    """Result endpoint should return HTTP 404 when the job does not exist."""
    response = client.get("/jobs/999999/result")

    assert response.status_code == 404
    assert response.json() == {"detail": "Job not found"}


@pytest.mark.parametrize("job_status", [JobStatus.PENDING, JobStatus.RUNNING, JobStatus.FAILED])
def test_get_job_result_returns_409_when_job_has_no_result(
    client: TestClient,
    session_factory: sessionmaker[Session],
    job_status: JobStatus,
) -> None:
    """Result endpoint should return HTTP 409 when the job exists without a result."""
    job_id = _create_job(session_factory, status=job_status)

    response = client.get(f"/jobs/{job_id}/result")

    assert response.status_code == 409
    assert response.json() == {"detail": "Job result is not available yet."}


def test_get_job_result_returns_curated_payload_for_completed_result(
    client: TestClient,
    session_factory: sessionmaker[Session],
) -> None:
    """Completed jobs should expose a curated public result payload."""
    job_id = _create_job(
        session_factory,
        status=JobStatus.COMPLETED,
        result_data={
            "transcript_text": "hello world",
            "transcript_json": {
                "segments": [
                    {"id": 0, "start": 0.0, "end": 0.5, "text": "hello"},
                    {"id": 1, "start": 0.5, "end": 1.0, "text": "world"},
                ],
                "language": "en",
                "engine": "faster-whisper",
                "model": "small",
            },
            "speaker_segments_json": [
                {"speaker": "SPEAKER_00", "start": 0.0, "end": 0.6},
                {"speaker": "SPEAKER_01", "start": 0.6, "end": 1.0},
            ],
            "detected_language": "en",
            "metadata_json": {
                "worker_id": "local-worker-1",
                "requested_device": "cpu",
                "resolved_device": "cpu",
                "compute_type": "int8",
                "empty_transcript": False,
                "diarization_attempted": True,
                "diarization_status": "completed",
            },
        },
    )

    response = client.get(f"/jobs/{job_id}/result")

    assert response.status_code == 200
    payload = response.json()
    _assert_public_result_contract(payload)
    assert payload == {
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


def test_get_job_result_keeps_empty_speaker_segments_list_on_success(
    client: TestClient,
    session_factory: sessionmaker[Session],
) -> None:
    """Successful diarization with zero valid segments should keep an empty list."""
    job_id = _create_job(
        session_factory,
        status=JobStatus.COMPLETED,
        result_data={
            "transcript_text": "hello",
            "transcript_json": {
                "segments": [{"id": 0, "start": 0.0, "end": 0.5, "text": "hello"}],
                "language": "en",
                "engine": "faster-whisper",
                "model": "small",
            },
            "speaker_segments_json": [],
            "detected_language": "en",
            "metadata_json": {
                "empty_transcript": False,
                "diarization_attempted": True,
                "diarization_status": "completed",
                "speaker_count": 0,
            },
        },
    )

    response = client.get(f"/jobs/{job_id}/result")

    assert response.status_code == 200
    payload = response.json()
    _assert_public_result_contract(payload)
    assert payload["speaker_segments_json"] == []
    assert payload["diarization_status"] == "completed"


def test_get_job_result_keeps_degraded_null_speaker_segments_without_leaking_internal_fields(
    client: TestClient,
    session_factory: sessionmaker[Session],
) -> None:
    """Degraded diarization should expose null speaker segments without internal failure details."""
    job_id = _create_job(
        session_factory,
        status=JobStatus.COMPLETED,
        result_data={
            "transcript_text": "hello world",
            "transcript_json": {
                "segments": [
                    {"id": 0, "start": 0.0, "end": 0.5, "text": "hello"},
                    {"id": 1, "start": 0.5, "end": 1.0, "text": "world"},
                ],
                "language": "en",
                "engine": "faster-whisper",
                "model": "small",
            },
            "speaker_segments_json": None,
            "detected_language": "en",
            "metadata_json": {
                "worker_id": "local-worker-1",
                "requested_device": "cuda",
                "resolved_device": "cuda",
                "compute_type": "float16",
                "empty_transcript": False,
                "diarization_attempted": True,
                "diarization_status": "failed",
                "diarization_error_code": "diarization_error",
                "diarization_error_message": "internal-only detail",
            },
        },
    )

    response = client.get(f"/jobs/{job_id}/result")

    assert response.status_code == 200
    payload = response.json()
    _assert_public_result_contract(payload)
    assert payload["speaker_segments_json"] is None
    assert payload["diarization_status"] == "failed"


def test_get_job_result_returns_200_for_valid_empty_transcript(
    client: TestClient,
    session_factory: sessionmaker[Session],
) -> None:
    """A valid empty transcript should still be exposed as a successful result."""
    job_id = _create_job(
        session_factory,
        status=JobStatus.COMPLETED,
        result_data={
            "transcript_text": "",
            "transcript_json": {
                "segments": [],
                "language": "en",
                "engine": "faster-whisper",
                "model": "small",
            },
            "speaker_segments_json": [],
            "detected_language": "en",
            "metadata_json": {
                "empty_transcript": True,
                "diarization_attempted": True,
                "diarization_status": "completed",
            },
        },
    )

    response = client.get(f"/jobs/{job_id}/result")

    assert response.status_code == 200
    payload = response.json()
    _assert_public_result_contract(payload)
    assert payload["transcript_text"] == ""
    assert payload["empty_transcript"] is True
    assert payload["transcript_json"]["segments"] == []


def test_get_job_result_uses_stable_fallbacks_for_legacy_incomplete_metadata(
    client: TestClient,
    session_factory: sessionmaker[Session],
) -> None:
    """Legacy rows with incomplete metadata should still expose a stable public result."""
    job_id = _create_job(
        session_factory,
        status=JobStatus.COMPLETED,
        result_data={
            "transcript_text": "legacy transcript",
            "transcript_json": {
                "engine": "faster-whisper",
                "model": "small",
            },
            "speaker_segments_json": None,
            "detected_language": "es",
            "metadata_json": {},
        },
    )

    response = client.get(f"/jobs/{job_id}/result")

    assert response.status_code == 200
    payload = response.json()
    _assert_public_result_contract(payload)
    assert payload == {
        "job_id": job_id,
        "transcript_text": "legacy transcript",
        "transcript_json": {
            "segments": [],
            "language": "es",
        },
        "speaker_segments_json": None,
        "detected_language": "es",
        "empty_transcript": False,
        "diarization_attempted": False,
        "diarization_status": None,
    }
