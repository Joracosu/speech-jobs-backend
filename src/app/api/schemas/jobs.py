"""Public response schemas for the jobs read API."""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from app.db.models import JobStatus


class JobRead(BaseModel):
    """Public read-only representation of a persisted job."""

    model_config = ConfigDict(from_attributes=True, extra="forbid")

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


class TranscriptRead(BaseModel):
    """Curated public transcript structure."""

    model_config = ConfigDict(extra="forbid")

    segments: list[dict[str, Any]]
    language: str | None


class JobResultRead(BaseModel):
    """Public read-only representation of a persisted job result."""

    model_config = ConfigDict(extra="forbid")

    job_id: int
    transcript_text: str
    transcript_json: TranscriptRead
    speaker_segments_json: list[dict[str, Any]] | None
    detected_language: str | None
    empty_transcript: bool
    diarization_attempted: bool
    diarization_status: Literal["completed", "failed"] | None
