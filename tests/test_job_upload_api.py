"""Tests for the public job upload API."""

from __future__ import annotations

from collections.abc import Iterator
from io import BytesIO
from pathlib import Path
import wave

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select
from sqlalchemy.orm import Session, sessionmaker

from app.api.dependencies import get_session_factory
from app.core.settings import get_settings
from app.db.models import Job, JobResult, JobStatus
from app.main import app


def _build_valid_wav_bytes() -> bytes:
    """Return a small valid WAV file generated in memory."""
    buffer = BytesIO()
    with wave.open(buffer, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(16000)
        wav_file.writeframes(b"\x00\x00" * 320)
    return buffer.getvalue()


@pytest.fixture()
def upload_settings(tmp_path: Path) -> Iterator[None]:
    """Isolate mutable upload settings and storage paths for each test."""
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
    """Provide an HTTP client after upload settings have been patched."""
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture()
def session_factory(upload_settings: None) -> sessionmaker[Session]:
    """Return the session factory after settings cache isolation."""
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


def test_upload_valid_audio_creates_pending_job_and_stores_file(
    client: TestClient,
    session_factory: sessionmaker[Session],
) -> None:
    """A valid upload should create a pending job and persist the source file."""
    wav_bytes = _build_valid_wav_bytes()

    response = client.post(
        "/jobs/upload",
        files={"file": ("sample.wav", wav_bytes, "audio/wav")},
    )

    assert response.status_code == 201
    payload = response.json()
    assert payload["status"] == "pending"
    assert payload["original_filename"] == "sample.wav"
    assert payload["profile_selected"] == "balanced"
    assert payload["device_used"] == "auto"

    with session_factory() as session:
        job = session.scalar(select(Job).where(Job.id == payload["id"]))
        assert job is not None
        assert job.status == JobStatus.PENDING
        assert Path(job.stored_path).exists()
        assert job.file_size_bytes == len(wav_bytes)
        assert job.input_sha256
        assert job.config_snapshot == {
            "profile": "balanced",
            "device_preference": "auto",
        }

    get_response = client.get(f"/jobs/{payload['id']}")
    assert get_response.status_code == 200
    assert get_response.json()["id"] == payload["id"]


def test_upload_rejects_invalid_extension(
    client: TestClient,
    session_factory: sessionmaker[Session],
) -> None:
    """Unsupported file extensions should be rejected before persistence."""
    response = client.post(
        "/jobs/upload",
        files={"file": ("notes.txt", b"plain text", "text/plain")},
    )

    assert response.status_code == 400
    assert response.json() == {"detail": "Unsupported audio file extension."}

    with session_factory() as session:
        assert session.scalar(select(Job.id).limit(1)) is None


def test_upload_rejects_oversized_file_early(
    client: TestClient,
    session_factory: sessionmaker[Session],
) -> None:
    """Uploads over the configured size limit should fail during streaming."""
    settings = get_settings()
    settings.max_upload_size_mb = 0

    response = client.post(
        "/jobs/upload",
        files={"file": ("too-large.wav", _build_valid_wav_bytes(), "audio/wav")},
    )

    assert response.status_code == 413
    assert response.json() == {
        "detail": "Uploaded file exceeds the configured size limit."
    }

    with session_factory() as session:
        assert session.scalar(select(Job.id).limit(1)) is None


def test_upload_rejects_invalid_audio_and_cleans_temp_files(
    client: TestClient,
    session_factory: sessionmaker[Session],
) -> None:
    """Invalid audio bytes should be rejected after ffprobe validation."""
    input_storage_dir = get_settings().input_storage_dir

    response = client.post(
        "/jobs/upload",
        files={"file": ("broken.wav", b"not-a-real-wave-file", "audio/wav")},
    )

    assert response.status_code == 400
    assert response.json() == {"detail": "Uploaded file is not valid audio."}
    assert not list(input_storage_dir.glob("*.part"))

    with session_factory() as session:
        assert session.scalar(select(Job.id).limit(1)) is None


def test_upload_reuses_existing_file_for_same_content(
    client: TestClient,
    session_factory: sessionmaker[Session],
) -> None:
    """Repeated uploads of the same content should reuse the hashed storage path."""
    wav_bytes = _build_valid_wav_bytes()

    first_response = client.post(
        "/jobs/upload",
        files={"file": ("dup.wav", wav_bytes, "audio/wav")},
    )
    second_response = client.post(
        "/jobs/upload",
        files={"file": ("dup.wav", wav_bytes, "audio/wav")},
    )

    assert first_response.status_code == 201
    assert second_response.status_code == 201

    with session_factory() as session:
        jobs = list(session.scalars(select(Job).order_by(Job.id.asc())).all())

    assert len(jobs) == 2
    assert jobs[0].stored_path == jobs[1].stored_path
    assert Path(jobs[0].stored_path).exists()
