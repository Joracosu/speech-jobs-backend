"""Public response schemas for the jobs read API."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.db.models import JobStatus


class JobRead(BaseModel):
    """Public read-only representation of a persisted job."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    status: JobStatus
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    updated_at: datetime
    original_filename: str
    file_size_bytes: int
    media_duration_seconds: float | None
    device_used: str
    profile_selected: str
    error_code: str | None
    error_message: str | None
