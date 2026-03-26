"""Tests for the read-only jobs API."""

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete
from sqlalchemy.orm import Session, sessionmaker

from app.api.dependencies import get_session_factory
from app.api.schemas.jobs import JobRead
from app.db.models import Job, JobResult, JobStatus
from app.main import app


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
