"""Worker-side TTL cleanup helpers for local input and artifact storage."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import logging
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.core.settings import Settings
from app.db import Job, JobStatus, utcnow


LOGGER = logging.getLogger(__name__)


@dataclass(slots=True, frozen=True)
class StorageCleanupReport:
    """Compact report for one storage cleanup pass."""

    input_files_processed: int
    input_files_deleted: int
    artifact_files_processed: int
    artifact_files_deleted: int
    artifact_dirs_deleted: int
    warnings: tuple[str, ...]

    @property
    def deleted_entries(self) -> int:
        """Return the total number of local filesystem entries deleted."""
        return (
            self.input_files_deleted
            + self.artifact_files_deleted
            + self.artifact_dirs_deleted
        )


def _build_cutoff(
    *,
    retention_days: int | None,
    category: str,
    cleanup_now: datetime,
    warnings: list[str],
) -> datetime | None:
    """Return the retention cutoff or None when cleanup is disabled.

    `retention_days=None` and negative values both disable the category in a
    safe, non-deleting way, while still surfacing one lightweight warning.
    """
    if retention_days is None:
        warnings.append(
            f"{category.capitalize()} cleanup is disabled because retention_days is not set."
        )
        return None

    if retention_days < 0:
        warnings.append(
            f"{category.capitalize()} cleanup is disabled because retention_days={retention_days}."
        )
        return None

    return cleanup_now - timedelta(days=retention_days)


def _resolve_root(root: Path) -> Path:
    """Return a canonicalized storage root path."""
    return root.resolve(strict=False)


def _resolve_safe_path(
    *,
    raw_path: str | Path | None,
    root: Path,
    category: str,
    warnings: list[str],
) -> Path | None:
    """Return a canonicalized path only when it stays under the configured root.

    Missing or blank path values are treated as benign legacy/anomalous input
    and skipped quietly. Unsafe or malformed concrete paths still produce a
    warning so cleanup reporting remains actionable without becoming noisy.
    """
    if raw_path is None:
        return None

    if isinstance(raw_path, str):
        normalized_raw_path = raw_path.strip()
        if not normalized_raw_path:
            return None
        candidate = Path(normalized_raw_path)
    else:
        candidate = raw_path

    try:
        resolved_root = _resolve_root(root)
        resolved_candidate = candidate.resolve(strict=False)
    except Exception as exc:
        warnings.append(
            f"Skipping unsafe {category} path '{raw_path}': unable to resolve it ({exc})."
        )
        return None

    if not resolved_candidate.is_relative_to(resolved_root):
        warnings.append(
            f"Skipping unsafe {category} path '{raw_path}': outside configured root '{resolved_root}'."
        )
        return None

    return resolved_candidate


def _terminal_timestamp(
    completed_at: datetime | None,
    updated_at: datetime | None,
) -> datetime | None:
    """Return the timestamp used to evaluate terminal-row expiry."""
    return completed_at or updated_at


def _cleanup_expired_input_files(
    *,
    session_factory: sessionmaker[Session],
    input_root: Path,
    retention_days: int | None,
    cleanup_now: datetime,
    warnings: list[str],
) -> tuple[int, int]:
    """Delete expired input files only when every referencing job is terminal and expired.

    Rows with missing or blank `stored_path` are skipped silently because they
    are anomalous data for cleanup purposes, not actionable path-safety events.
    """
    if not input_root.exists():
        return 0, 0

    cutoff = _build_cutoff(
        retention_days=retention_days,
        category="input",
        cleanup_now=cleanup_now,
        warnings=warnings,
    )
    if cutoff is None:
        return 0, 0

    try:
        with session_factory() as session:
            rows = session.execute(
                select(
                    Job.stored_path,
                    Job.status,
                    Job.completed_at,
                    Job.updated_at,
                )
            ).all()
    except Exception as exc:
        warnings.append(f"Input cleanup query failed: {exc}")
        return 0, 0

    grouped_candidates: dict[Path, list[tuple[JobStatus, datetime | None]]] = {}
    for stored_path, job_status, completed_at, updated_at in rows:
        safe_path = _resolve_safe_path(
            raw_path=stored_path,
            root=input_root,
            category="input",
            warnings=warnings,
        )
        if safe_path is None:
            continue

        grouped_candidates.setdefault(safe_path, []).append(
            (job_status, _terminal_timestamp(completed_at, updated_at))
        )

    processed_files = len(grouped_candidates)
    deleted_files = 0
    for file_path, references in grouped_candidates.items():
        if any(
            job_status not in {JobStatus.COMPLETED, JobStatus.FAILED}
            for job_status, _ in references
        ):
            continue

        if any(reference_timestamp is None for _, reference_timestamp in references):
            continue

        if any(
            reference_timestamp > cutoff
            for _, reference_timestamp in references
            if reference_timestamp is not None
        ):
            continue

        try:
            if not file_path.exists():
                continue
            if not file_path.is_file():
                warnings.append(
                    f"Skipping unsafe input path '{file_path}': expected a file."
                )
                continue
            file_path.unlink()
            deleted_files += 1
        except FileNotFoundError:
            continue
        except Exception as exc:
            warnings.append(f"Failed to delete expired input file '{file_path}': {exc}")

    return processed_files, deleted_files


def _cleanup_expired_artifacts(
    *,
    artifact_root: Path,
    retention_days: int | None,
    cleanup_now: datetime,
    warnings: list[str],
    store_intermediate_artifacts: bool,
) -> tuple[int, int, int]:
    """Delete expired artifact files and prune empty directories."""
    if not store_intermediate_artifacts or not artifact_root.exists():
        return 0, 0, 0

    cutoff = _build_cutoff(
        retention_days=retention_days,
        category="artifact",
        cleanup_now=cleanup_now,
        warnings=warnings,
    )
    if cutoff is None:
        return 0, 0, 0

    try:
        artifact_paths = list(artifact_root.rglob("*"))
    except Exception as exc:
        warnings.append(f"Artifact cleanup traversal failed: {exc}")
        return 0, 0, 0

    processed_files = 0
    deleted_files = 0
    deleted_dirs = 0

    for artifact_path in artifact_paths:
        if not artifact_path.is_file():
            continue

        safe_path = _resolve_safe_path(
            raw_path=artifact_path,
            root=artifact_root,
            category="artifact",
            warnings=warnings,
        )
        if safe_path is None:
            continue

        processed_files += 1
        try:
            modified_at = datetime.fromtimestamp(
                safe_path.stat().st_mtime,
                tz=UTC,
            )
        except FileNotFoundError:
            continue
        except Exception as exc:
            warnings.append(
                f"Unable to inspect artifact file '{safe_path}' during cleanup: {exc}"
            )
            continue

        if modified_at > cutoff:
            continue

        try:
            safe_path.unlink()
            deleted_files += 1
        except FileNotFoundError:
            continue
        except Exception as exc:
            warnings.append(f"Failed to delete expired artifact '{safe_path}': {exc}")

    for artifact_path in sorted(
        artifact_paths,
        key=lambda path: len(path.parts),
        reverse=True,
    ):
        if not artifact_path.is_dir():
            continue

        safe_path = _resolve_safe_path(
            raw_path=artifact_path,
            root=artifact_root,
            category="artifact",
            warnings=warnings,
        )
        if safe_path is None or safe_path == _resolve_root(artifact_root):
            continue

        try:
            next(safe_path.iterdir())
        except StopIteration:
            try:
                safe_path.rmdir()
                deleted_dirs += 1
            except FileNotFoundError:
                continue
            except Exception as exc:
                warnings.append(
                    f"Failed to remove empty artifact directory '{safe_path}': {exc}"
                )
        except FileNotFoundError:
            continue
        except Exception:
            continue

    return processed_files, deleted_files, deleted_dirs


def run_storage_cleanup(
    *,
    session_factory: sessionmaker[Session],
    settings: Settings,
    now: datetime | None = None,
) -> StorageCleanupReport:
    """Run one best-effort local storage cleanup pass."""
    cleanup_now = now or utcnow()
    warnings: list[str] = []

    input_files_processed, input_files_deleted = _cleanup_expired_input_files(
        session_factory=session_factory,
        input_root=settings.input_storage_dir,
        retention_days=settings.input_retention_days,
        cleanup_now=cleanup_now,
        warnings=warnings,
    )
    (
        artifact_files_processed,
        artifact_files_deleted,
        artifact_dirs_deleted,
    ) = _cleanup_expired_artifacts(
        artifact_root=settings.artifact_storage_dir,
        retention_days=settings.artifact_retention_days,
        cleanup_now=cleanup_now,
        warnings=warnings,
        store_intermediate_artifacts=settings.store_intermediate_artifacts,
    )

    return StorageCleanupReport(
        input_files_processed=input_files_processed,
        input_files_deleted=input_files_deleted,
        artifact_files_processed=artifact_files_processed,
        artifact_files_deleted=artifact_files_deleted,
        artifact_dirs_deleted=artifact_dirs_deleted,
        warnings=tuple(warnings),
    )


def log_storage_cleanup_report(
    *,
    logger: logging.Logger,
    trigger: str,
    report: StorageCleanupReport,
) -> None:
    """Log one compact summary for a cleanup pass."""
    logger.info(
        "Storage cleanup (%s): input processed=%s deleted=%s, artifact processed=%s deleted=%s, artifact_dirs_deleted=%s, warnings=%s",
        trigger,
        report.input_files_processed,
        report.input_files_deleted,
        report.artifact_files_processed,
        report.artifact_files_deleted,
        report.artifact_dirs_deleted,
        len(report.warnings),
    )
    for warning_message in report.warnings:
        logger.warning("Storage cleanup (%s): %s", trigger, warning_message)
