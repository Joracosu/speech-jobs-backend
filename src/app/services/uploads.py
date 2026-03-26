"""Helpers for validating, storing, and describing uploaded audio files."""

from __future__ import annotations

import hashlib
import secrets
import subprocess
from dataclasses import dataclass
from pathlib import Path

from fastapi import UploadFile, status

from app.core.settings import Settings

CHUNK_SIZE_BYTES = 1024 * 1024
FFPROBE_TIMEOUT_SECONDS = 10
ACCEPTED_CONTENT_TYPES = {
    "application/octet-stream",
    "video/mp4",
}


class UploadValidationError(Exception):
    """Represent a client-facing upload validation failure."""

    def __init__(self, detail: str, status_code: int) -> None:
        super().__init__(detail)
        self.detail = detail
        self.status_code = status_code


@dataclass(slots=True)
class StoredUpload:
    """Describe a validated upload stored in local input storage."""

    original_filename: str
    stored_path: Path
    input_sha256: str
    file_size_bytes: int
    media_duration_seconds: float | None
    created_new_file: bool


def _sanitize_original_filename(filename: str | None) -> str:
    """Return a safe basename for the uploaded filename."""
    sanitized_name = Path(filename or "").name
    if not sanitized_name:
        raise UploadValidationError(
            detail="Uploaded file must include a filename.",
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    return sanitized_name


def _validate_extension(filename: str, settings: Settings) -> str:
    """Validate the upload suffix against the configured allowlist."""
    suffix = Path(filename).suffix.lower()
    if not suffix or suffix.lstrip(".") not in settings.allowed_audio_extensions:
        raise UploadValidationError(
            detail="Unsupported audio file extension.",
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    return suffix


def _validate_content_type(content_type: str | None) -> None:
    """Validate the upload content type as a soft compatibility signal."""
    if not content_type:
        return

    normalized_type = content_type.lower()
    if normalized_type.startswith("audio/"):
        return
    if normalized_type in ACCEPTED_CONTENT_TYPES:
        return

    raise UploadValidationError(
        detail="Unsupported upload content type.",
        status_code=status.HTTP_400_BAD_REQUEST,
    )


def _probe_audio_duration(path: Path) -> float | None:
    """Validate the stored upload with ffprobe and return duration if available."""
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=FFPROBE_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise UploadValidationError(
            detail="Uploaded file is not valid audio.",
            status_code=status.HTTP_400_BAD_REQUEST,
        ) from exc

    if result.returncode != 0:
        raise UploadValidationError(
            detail="Uploaded file is not valid audio.",
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    duration_text = result.stdout.strip()
    if not duration_text:
        return None

    try:
        return float(duration_text)
    except ValueError:
        return None


async def store_uploaded_audio(
    upload_file: UploadFile,
    settings: Settings,
) -> StoredUpload:
    """Validate and store an uploaded audio file under the configured input path."""
    original_filename = _sanitize_original_filename(upload_file.filename)
    suffix = _validate_extension(original_filename, settings)
    _validate_content_type(upload_file.content_type)

    input_directory = settings.input_storage_dir
    input_directory.mkdir(parents=True, exist_ok=True)

    temp_path = input_directory / f".upload-{secrets.token_hex(8)}{suffix}.part"
    max_size_bytes = settings.max_upload_size_mb * 1024 * 1024
    file_size_bytes = 0
    hasher = hashlib.sha256()

    try:
        with temp_path.open("wb") as temp_file:
            while chunk := await upload_file.read(CHUNK_SIZE_BYTES):
                file_size_bytes += len(chunk)
                if file_size_bytes > max_size_bytes:
                    temp_file.close()
                    temp_path.unlink(missing_ok=True)
                    raise UploadValidationError(
                        detail="Uploaded file exceeds the configured size limit.",
                        status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                    )
                hasher.update(chunk)
                temp_file.write(chunk)
    finally:
        await upload_file.close()

    try:
        media_duration_seconds = _probe_audio_duration(temp_path)
    except UploadValidationError:
        temp_path.unlink(missing_ok=True)
        raise

    final_path = input_directory / f"{hasher.hexdigest()}{suffix}"
    created_new_file = False

    if final_path.exists():
        temp_path.unlink(missing_ok=True)
    else:
        temp_path.replace(final_path)
        created_new_file = True

    return StoredUpload(
        original_filename=original_filename,
        stored_path=final_path,
        input_sha256=hasher.hexdigest(),
        file_size_bytes=file_size_bytes,
        media_duration_seconds=media_duration_seconds,
        created_new_file=created_new_file,
    )
