"""Read-only job endpoints backed by the persistence layer."""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.api.dependencies import get_db_session
from app.api.schemas.jobs import JobRead, JobResultRead, TranscriptRead
from app.core.settings import Settings, get_settings
from app.db.models import Job, JobResult, JobStatus
from app.services.uploads import UploadValidationError, store_uploaded_audio

router = APIRouter(prefix="/jobs", tags=["jobs"])
ALLOWED_PROFILES = {"fast", "balanced", "accurate"}
ALLOWED_DEVICE_PREFERENCES = {"auto", "cpu", "cuda"}
JOB_NOT_FOUND_DETAIL = "Job not found"
JOB_RESULT_NOT_AVAILABLE_DETAIL = "Job result is not available yet."


def _normalize_language(value: object) -> str | None:
    """Return a normalized optional language code string."""
    if not isinstance(value, str):
        return None

    normalized_value = value.strip()
    return normalized_value or None


def _resolve_public_language(job_result: JobResult) -> str | None:
    """Return one public language value kept consistent across the payload."""
    transcript_json = job_result.transcript_json
    transcript_language = None
    if isinstance(transcript_json, dict):
        transcript_language = _normalize_language(transcript_json.get("language"))

    return _normalize_language(job_result.detected_language) or transcript_language


def _build_public_transcript_json(
    job_result: JobResult,
    public_language: str | None,
) -> TranscriptRead:
    """Project the persisted transcript JSON to the public transcript shape."""
    transcript_json = job_result.transcript_json
    segments: list[dict[str, Any]] = []
    if isinstance(transcript_json, dict) and isinstance(transcript_json.get("segments"), list):
        segments = list(transcript_json["segments"])

    return TranscriptRead(
        segments=segments,
        language=public_language,
    )


def _build_job_result_read(job: Job, job_result: JobResult) -> JobResultRead:
    """Project one persisted job result to the public API contract."""
    metadata_json = (
        job_result.metadata_json if isinstance(job_result.metadata_json, dict) else {}
    )
    public_language = _resolve_public_language(job_result)

    empty_transcript = metadata_json.get("empty_transcript")
    if not isinstance(empty_transcript, bool):
        empty_transcript = job_result.transcript_text == ""

    diarization_attempted = metadata_json.get("diarization_attempted")
    if not isinstance(diarization_attempted, bool):
        diarization_attempted = False

    diarization_status = metadata_json.get("diarization_status")
    if diarization_status not in {"completed", "failed"}:
        diarization_status = None

    return JobResultRead(
        job_id=job.id,
        transcript_text=job_result.transcript_text,
        transcript_json=_build_public_transcript_json(job_result, public_language),
        speaker_segments_json=job_result.speaker_segments_json,
        detected_language=public_language,
        empty_transcript=empty_transcript,
        diarization_attempted=diarization_attempted,
        diarization_status=diarization_status,
    )


@router.get("", response_model=list[JobRead])
def list_jobs(
    session: Annotated[Session, Depends(get_db_session)],
) -> list[Job]:
    """Return persisted jobs ordered from newest to oldest."""
    statement = select(Job).order_by(Job.id.desc())
    return list(session.scalars(statement).all())


@router.get("/{job_id}", response_model=JobRead)
def get_job(
    job_id: int,
    session: Annotated[Session, Depends(get_db_session)],
) -> Job:
    """Return one persisted job or a 404 error when it does not exist."""
    statement = select(Job).where(Job.id == job_id)
    job = session.scalar(statement)
    if job is None:
        raise HTTPException(status_code=404, detail=JOB_NOT_FOUND_DETAIL)
    return job


@router.get("/{job_id}/result", response_model=JobResultRead)
def get_job_result(
    job_id: int,
    session: Annotated[Session, Depends(get_db_session)],
) -> JobResultRead:
    """Return the curated public result for one persisted job."""
    statement = (
        select(Job)
        .where(Job.id == job_id)
        .options(selectinload(Job.result))
    )
    job = session.scalar(statement)
    if job is None:
        raise HTTPException(status_code=404, detail=JOB_NOT_FOUND_DETAIL)

    if job.result is None:
        raise HTTPException(status_code=409, detail=JOB_RESULT_NOT_AVAILABLE_DETAIL)

    return _build_job_result_read(job, job.result)


@router.post("/upload", response_model=JobRead, status_code=status.HTTP_201_CREATED)
async def upload_job(
    file: Annotated[UploadFile, File(...)],
    profile: Annotated[str | None, Form()] = None,
    device_preference: Annotated[str | None, Form()] = None,
    session: Annotated[Session, Depends(get_db_session)] = None,
    settings: Annotated[Settings, Depends(get_settings)] = None,
) -> Job:
    """Validate an uploaded audio file, store it locally, and create a pending job."""
    selected_profile = profile or settings.default_profile
    selected_device_preference = device_preference or settings.device_preference

    if selected_profile not in ALLOWED_PROFILES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Unsupported processing profile.",
        )
    if selected_device_preference not in ALLOWED_DEVICE_PREFERENCES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Unsupported device preference.",
        )

    try:
        stored_upload = await store_uploaded_audio(file, settings)
    except UploadValidationError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc

    job = Job(
        status=JobStatus.PENDING,
        original_filename=stored_upload.original_filename,
        stored_path=stored_upload.stored_path.as_posix(),
        input_sha256=stored_upload.input_sha256,
        file_size_bytes=stored_upload.file_size_bytes,
        media_duration_seconds=stored_upload.media_duration_seconds,
        device_used=selected_device_preference,
        profile_selected=selected_profile,
        config_snapshot={
            "profile": selected_profile,
            "device_preference": selected_device_preference,
        },
        error_code=None,
        error_message=None,
    )

    session.add(job)
    try:
        session.commit()
    except Exception:
        session.rollback()
        if stored_upload.created_new_file:
            stored_upload.stored_path.unlink(missing_ok=True)
        raise

    session.refresh(job)
    return job
