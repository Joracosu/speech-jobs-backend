"""Read-only job endpoints backed by the persistence layer."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.dependencies import get_db_session
from app.api.schemas.jobs import JobRead
from app.db.models import Job

router = APIRouter(prefix="/jobs", tags=["jobs"])


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
