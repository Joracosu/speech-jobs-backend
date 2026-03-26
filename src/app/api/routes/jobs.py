"""Read-only job endpoints backed by the persistence layer."""

from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.dependencies import get_db_session
from app.api.schemas.jobs import JobRead
from app.core.settings import Settings, get_settings
from app.db.models import Job
from app.db.models import JobStatus
from app.services.uploads import UploadValidationError, store_uploaded_audio

router = APIRouter(prefix="/jobs", tags=["jobs"])
ALLOWED_PROFILES = {"fast", "balanced", "accurate"}
ALLOWED_DEVICE_PREFERENCES = {"auto", "cpu", "cuda"}


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
        raise HTTPException(status_code=404, detail="Job not found")
    return job


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
