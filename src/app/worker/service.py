"""Worker lifecycle helpers for claiming and processing jobs."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta
import logging
from dataclasses import dataclass
from pathlib import Path
from threading import Event, Thread
from time import perf_counter, sleep

from sqlalchemy import select, update
from sqlalchemy.orm import Session, selectinload, sessionmaker

from app.core.settings import Settings, get_settings
from app.db import Job, JobResult, JobStatus, create_session_factory, utcnow
from app.worker.asr import AsrExecutionError, AsrTranscriptionResult, transcribe_audio
from app.worker.cleanup import log_storage_cleanup_report, run_storage_cleanup
from app.worker.diarization import (
    DiarizationExecutionError,
    DiarizationResult,
    diarize_audio,
)
from app.worker.silence import SilenceClassification, inspect_audio_silence


LOGGER = logging.getLogger(__name__)
DEFAULT_WORKER_HEARTBEAT_INTERVAL_SECONDS = 10.0
DEFAULT_WORKER_STALE_AFTER_SECONDS = 60.0
MIN_COORDINATION_INTERVAL_SECONDS = 0.01
PROCESSING_INTERRUPTED_ERROR_MESSAGE = (
    "Worker processing was interrupted before completion."
)


def _elapsed_ms(start_time: float) -> int:
    """Return elapsed milliseconds from a monotonic start time."""
    return max(0, int((perf_counter() - start_time) * 1000))


def _get_heartbeat_interval_seconds(settings: Settings | object) -> float:
    """Return the effective heartbeat interval in seconds."""
    interval = getattr(
        settings,
        "worker_heartbeat_interval_seconds",
        DEFAULT_WORKER_HEARTBEAT_INTERVAL_SECONDS,
    )
    try:
        return max(float(interval), MIN_COORDINATION_INTERVAL_SECONDS)
    except (TypeError, ValueError):
        return DEFAULT_WORKER_HEARTBEAT_INTERVAL_SECONDS


def _get_stale_after_seconds(settings: Settings | object) -> float:
    """Return the effective stale timeout in seconds."""
    stale_after = getattr(
        settings,
        "worker_stale_after_seconds",
        DEFAULT_WORKER_STALE_AFTER_SECONDS,
    )
    try:
        return max(float(stale_after), MIN_COORDINATION_INTERVAL_SECONDS)
    except (TypeError, ValueError):
        return DEFAULT_WORKER_STALE_AFTER_SECONDS


def _get_reconciliation_interval_seconds(settings: Settings | object) -> float:
    """Return the lightweight cadence used for steady-state reconciliation."""
    heartbeat_interval = _get_heartbeat_interval_seconds(settings)
    stale_after = _get_stale_after_seconds(settings)
    return max(heartbeat_interval, stale_after / 2, MIN_COORDINATION_INTERVAL_SECONDS)


@dataclass(slots=True, frozen=True)
class ReconciliationReport:
    """Compact summary for one stale-running reconciliation pass."""

    scanned_jobs: int
    stale_jobs: int
    failed_jobs: int
    completed_jobs: int
    duration_ms: int


def log_reconciliation_report(
    logger: logging.Logger,
    trigger: str,
    report: ReconciliationReport,
) -> None:
    """Log one compact reconciliation summary."""
    logger.info(
        "Running recovery trigger=%s scanned=%s stale=%s failed=%s completed=%s duration_ms=%s",
        trigger,
        report.scanned_jobs,
        report.stale_jobs,
        report.failed_jobs,
        report.completed_jobs,
        report.duration_ms,
    )


def _update_job_heartbeat(
    session_factory: sessionmaker[Session],
    job_id: int,
    beat_time: datetime | None = None,
) -> bool:
    """Refresh the heartbeat only while the selected job is still running."""
    effective_beat_time = beat_time or utcnow()
    with session_factory() as session, session.begin():
        result = session.execute(
            update(Job)
            .where(Job.id == job_id, Job.status == JobStatus.RUNNING)
            .values(
                last_heartbeat_at=effective_beat_time,
                updated_at=effective_beat_time,
            )
        )
        return result.rowcount > 0


class _JobHeartbeat:
    """Small background heartbeat loop for one actively processed job."""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        job_id: int,
        interval_seconds: float,
    ) -> None:
        self._session_factory = session_factory
        self._job_id = job_id
        self._interval_seconds = interval_seconds
        self._stop_event = Event()
        self._thread = Thread(
            target=self._run,
            name=f"job-heartbeat-{job_id}",
            daemon=True,
        )

    def start(self) -> None:
        """Start the daemon heartbeat loop."""
        self._thread.start()

    def stop(self) -> None:
        """Stop the heartbeat loop and wait briefly for thread exit."""
        self._stop_event.set()
        self._thread.join(timeout=self._interval_seconds + 0.5)

    def _run(self) -> None:
        """Refresh job liveness until the job is no longer running."""
        while not self._stop_event.wait(self._interval_seconds):
            if not _update_job_heartbeat(self._session_factory, self._job_id):
                return


@contextmanager
def _job_heartbeat_context(
    session_factory: sessionmaker[Session],
    job_id: int,
    settings: Settings | object,
):
    """Keep one claimed job fresh while it is actively being processed."""
    heartbeat = _JobHeartbeat(
        session_factory=session_factory,
        job_id=job_id,
        interval_seconds=_get_heartbeat_interval_seconds(settings),
    )
    heartbeat.start()
    try:
        yield
    finally:
        heartbeat.stop()


def _resolve_job_liveness_timestamp(job: Job) -> tuple[datetime | None, bool]:
    """Return the effective liveness timestamp plus anomaly marker."""
    if job.last_heartbeat_at is not None:
        return job.last_heartbeat_at, False
    if job.started_at is not None:
        return job.started_at, False
    return None, True


def reconcile_stale_running_jobs(
    session_factory: sessionmaker[Session],
    settings: Settings | object,
    *,
    trigger: str,
    now: datetime | None = None,
) -> ReconciliationReport:
    """Reconcile stale running jobs to explicit terminal states."""
    started_at = perf_counter()
    effective_now = now or utcnow()
    stale_before = effective_now - timedelta(seconds=_get_stale_after_seconds(settings))
    scanned_jobs = 0
    stale_jobs = 0
    failed_jobs = 0
    completed_jobs = 0

    with session_factory() as session, session.begin():
        running_jobs = list(
            session.scalars(
                select(Job)
                .where(Job.status == JobStatus.RUNNING)
                .options(selectinload(Job.result))
                .with_for_update(skip_locked=True)
            ).all()
        )
        scanned_jobs = len(running_jobs)

        for job in running_jobs:
            liveness_timestamp, missing_liveness = _resolve_job_liveness_timestamp(job)
            if missing_liveness:
                LOGGER.warning(
                    "Running recovery trigger=%s job_id=%s anomaly=missing_liveness_timestamps",
                    trigger,
                    job.id,
                )
            elif liveness_timestamp is not None and liveness_timestamp >= stale_before:
                continue

            stale_jobs += 1
            if job.result is None:
                job.status = JobStatus.FAILED
                if job.completed_at is None:
                    job.completed_at = effective_now
                job.error_code = "processing_interrupted"
                job.error_message = PROCESSING_INTERRUPTED_ERROR_MESSAGE
                failed_jobs += 1
                continue

            job.status = JobStatus.COMPLETED
            if job.completed_at is None:
                job.completed_at = effective_now
            job.error_code = None
            job.error_message = None
            completed_jobs += 1

    return ReconciliationReport(
        scanned_jobs=scanned_jobs,
        stale_jobs=stale_jobs,
        failed_jobs=failed_jobs,
        completed_jobs=completed_jobs,
        duration_ms=_elapsed_ms(started_at),
    )


def claim_next_pending_job(session_factory: sessionmaker[Session]) -> int | None:
    """Claim the oldest pending job using a PostgreSQL-safe row lock."""
    with session_factory() as session, session.begin():
        statement = (
            select(Job)
            .where(Job.status == JobStatus.PENDING)
            .order_by(Job.created_at.asc(), Job.id.asc())
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        job = session.scalar(statement)
        if job is None:
            return None

        heartbeat_at = utcnow()
        job.status = JobStatus.RUNNING
        job.started_at = heartbeat_at
        job.last_heartbeat_at = heartbeat_at
        job.error_code = None
        job.error_message = None
        session.flush()
        LOGGER.info("Claimed pending job id=%s", job.id)
        return job.id


def _mark_job_failed(
    session_factory: sessionmaker[Session],
    job_id: int,
    error_code: str,
    error_message: str,
) -> None:
    """Persist a failed terminal state for the selected job."""
    with session_factory() as session, session.begin():
        job = session.get(Job, job_id)
        if job is None:
            return

        job.status = JobStatus.FAILED
        job.completed_at = utcnow()
        job.error_code = error_code
        job.error_message = error_message


@dataclass(slots=True, frozen=True)
class ClaimedJobContext:
    """Fields needed to run ASR outside the database transaction."""

    input_path: Path
    profile: str
    requested_device: str


def _build_completed_metadata(
    settings: Settings,
    profile: str,
    asr_result: AsrTranscriptionResult,
) -> dict[str, object]:
    """Return the shared metadata payload for a completed job."""
    return {
        "engine": asr_result.metadata_json["engine"],
        "profile": profile,
        "model": asr_result.metadata_json["model"],
        "requested_device": asr_result.metadata_json["requested_device"],
        "resolved_device": asr_result.metadata_json["resolved_device"],
        "compute_type": asr_result.metadata_json["compute_type"],
        "worker_id": settings.worker_id,
        "empty_transcript": asr_result.metadata_json["empty_transcript"],
    }


def _build_completed_metadata_with_diarization(
    settings: Settings,
    profile: str,
    asr_result: AsrTranscriptionResult,
    diarization_result: DiarizationResult,
) -> dict[str, object]:
    """Return the final metadata payload for a completed ASR+diarization job."""
    return {
        "mode": "asr+diarization",
        **_build_completed_metadata(
            settings=settings,
            profile=profile,
            asr_result=asr_result,
        ),
        "diarization_attempted": True,
        "diarization_status": "completed",
        "diarization_enabled": diarization_result.metadata_json["diarization_enabled"],
        "diarization_model_id": diarization_result.metadata_json["diarization_model_id"],
        "diarization_device": diarization_result.metadata_json["diarization_device"],
        "speaker_count": diarization_result.metadata_json["speaker_count"],
    }


def _build_completed_metadata_with_degraded_diarization(
    settings: Settings,
    profile: str,
    asr_result: AsrTranscriptionResult,
    diarization_error_message: str,
) -> dict[str, object]:
    """Return the final metadata payload for a completed ASR result with degraded diarization."""
    return {
        "mode": "asr",
        **_build_completed_metadata(
            settings=settings,
            profile=profile,
            asr_result=asr_result,
        ),
        "diarization_attempted": True,
        "diarization_status": "failed",
        "diarization_error_code": "diarization_error",
        "diarization_error_message": diarization_error_message,
    }


def _build_completed_metadata_for_silence(
    settings: Settings,
    profile: str,
    requested_device: str,
) -> dict[str, object]:
    """Return the internal metadata for a silence short-circuit result."""
    return {
        "mode": "silence_short_circuit",
        "profile": profile,
        "requested_device": requested_device,
        "worker_id": settings.worker_id,
        "empty_transcript": True,
        "diarization_attempted": False,
        "result_origin": "silence_short_circuit",
    }


def _load_claimed_job_context(
    session_factory: sessionmaker[Session],
    job_id: int,
) -> ClaimedJobContext | None:
    """Load the claimed job and fail it early if preconditions are broken."""
    with session_factory() as session, session.begin():
        job = session.get(Job, job_id)
        if job is None:
            return None

        if job.result is not None:
            job.status = JobStatus.FAILED
            job.completed_at = utcnow()
            job.error_code = "processing_error"
            job.error_message = "Job already has a persisted result."
            return None

        input_path = Path(job.stored_path)
        if not input_path.exists():
            job.status = JobStatus.FAILED
            job.completed_at = utcnow()
            job.error_code = "missing_input_file"
            job.error_message = (
                f"Input file does not exist at '{job.stored_path}'."
            )
            return None

        return ClaimedJobContext(
            input_path=input_path,
            profile=job.profile_selected,
            requested_device=job.device_used,
        )


def _persist_completed_job(
    session_factory: sessionmaker[Session],
    job_id: int,
    transcript_text: str,
    transcript_json: dict[str, object],
    speaker_segments_json: list[dict[str, object]] | None,
    detected_language: str | None,
    metadata_json: dict[str, object],
) -> None:
    """Persist the completed job result and terminal state atomically."""
    with session_factory() as session, session.begin():
        job = session.get(Job, job_id)
        if job is None:
            return

        if job.result is not None:
            job.status = JobStatus.FAILED
            job.completed_at = utcnow()
            job.error_code = "processing_error"
            job.error_message = "Job already has a persisted result."
            return

        job.result = JobResult(
            transcript_text=transcript_text,
            transcript_json=transcript_json,
            speaker_segments_json=speaker_segments_json,
            detected_language=detected_language,
            metadata_json=metadata_json,
        )
        job.status = JobStatus.COMPLETED
        job.completed_at = utcnow()
        job.error_code = None
        job.error_message = None


def process_claimed_job(
    session_factory: sessionmaker[Session],
    job_id: int,
    settings: Settings,
) -> None:
    """Process one claimed job using real ASR and atomic terminal state updates."""
    job_started_at = perf_counter()
    try:
        job_context = _load_claimed_job_context(session_factory, job_id)
        if job_context is None:
            with session_factory() as session:
                failed_job = session.get(Job, job_id)
                if failed_job is not None and failed_job.status == JobStatus.FAILED:
                    LOGGER.error(
                        "Job id=%s finished with terminal failure error_code=%s duration_ms=%s",
                        job_id,
                        failed_job.error_code,
                        _elapsed_ms(job_started_at),
                    )
            return

        with _job_heartbeat_context(
            session_factory=session_factory,
            job_id=job_id,
            settings=settings,
        ):
            silence_result = inspect_audio_silence(job_context.input_path)
            if silence_result.classification == SilenceClassification.SILENCE:
                metadata_json = _build_completed_metadata_for_silence(
                    settings=settings,
                    profile=job_context.profile,
                    requested_device=job_context.requested_device,
                )
                try:
                    _persist_completed_job(
                        session_factory=session_factory,
                        job_id=job_id,
                        transcript_text="",
                        transcript_json={"segments": [], "language": None},
                        speaker_segments_json=[],
                        detected_language=None,
                        metadata_json=metadata_json,
                    )
                except Exception as exc:
                    LOGGER.exception(
                        "Failed to persist completed silence result for job id=%s error_code=processing_error",
                        job_id,
                    )
                    _mark_job_failed(
                        session_factory=session_factory,
                        job_id=job_id,
                        error_code="processing_error",
                        error_message=f"Failed to persist processing result: {exc}",
                    )
                    LOGGER.error(
                        "Job id=%s finished with terminal failure error_code=processing_error duration_ms=%s",
                        job_id,
                        _elapsed_ms(job_started_at),
                    )
                    return

                LOGGER.info(
                    "Job id=%s finished successfully result_origin=silence_short_circuit empty_transcript=true duration_ms=%s",
                    job_id,
                    _elapsed_ms(job_started_at),
                )
                return

            if silence_result.classification == SilenceClassification.INCONCLUSIVE:
                LOGGER.info(
                    "Silence precheck inconclusive for job id=%s fallback=asr detail=%s",
                    job_id,
                    silence_result.detail,
                )

            asr_started_at = perf_counter()
            LOGGER.info(
                "ASR started for job id=%s requested_device=%s profile=%s",
                job_id,
                job_context.requested_device,
                job_context.profile,
            )
            asr_result = transcribe_audio(
                audio_path=job_context.input_path,
                profile=job_context.profile,
                requested_device=job_context.requested_device,
            )
            LOGGER.info(
                "ASR completed for job id=%s requested_device=%s resolved_device=%s duration_ms=%s",
                job_id,
                job_context.requested_device,
                asr_result.metadata_json["resolved_device"],
                _elapsed_ms(asr_started_at),
            )
    except AsrExecutionError as exc:
        LOGGER.error(
            "ASR failed for job id=%s requested_device=%s error_code=asr_error duration_ms=%s: %s",
            job_id,
            job_context.requested_device if "job_context" in locals() and job_context is not None else "unknown",
            _elapsed_ms(asr_started_at) if "asr_started_at" in locals() else _elapsed_ms(job_started_at),
            exc,
        )
        _mark_job_failed(
            session_factory=session_factory,
            job_id=job_id,
            error_code="asr_error",
            error_message=str(exc),
        )
        LOGGER.error(
            "Job id=%s finished with terminal failure error_code=asr_error duration_ms=%s",
            job_id,
            _elapsed_ms(job_started_at),
        )
        return
    except Exception as exc:
        LOGGER.exception(
            "ASR crashed for job id=%s requested_device=%s error_code=processing_error duration_ms=%s",
            job_id,
            job_context.requested_device if "job_context" in locals() and job_context is not None else "unknown",
            _elapsed_ms(asr_started_at) if "asr_started_at" in locals() else _elapsed_ms(job_started_at),
        )
        _mark_job_failed(
            session_factory=session_factory,
            job_id=job_id,
            error_code="processing_error",
            error_message=f"Worker processing failed: {exc}",
        )
        LOGGER.error(
            "Job id=%s finished with terminal failure error_code=processing_error duration_ms=%s",
            job_id,
            _elapsed_ms(job_started_at),
        )
        return

    try:
        diarization_started_at = perf_counter()
        LOGGER.info(
            "Diarization started for job id=%s requested_device=%s",
            job_id,
            job_context.requested_device,
        )
        diarization_result = diarize_audio(
            audio_path=job_context.input_path,
            requested_device=job_context.requested_device,
            model_id=settings.diarization_model_id,
            huggingface_token=settings.huggingface_token,
        )
        LOGGER.info(
            "Diarization completed for job id=%s requested_device=%s resolved_device=%s diarization_status=completed duration_ms=%s",
            job_id,
            job_context.requested_device,
            diarization_result.metadata_json["diarization_device"],
            _elapsed_ms(diarization_started_at),
        )
    except DiarizationExecutionError as exc:
        LOGGER.warning(
            "Diarization degraded for job id=%s requested_device=%s diarization_status=failed duration_ms=%s: %s",
            job_id,
            job_context.requested_device,
            _elapsed_ms(diarization_started_at),
            exc,
        )
        speaker_segments_json: list[dict[str, object]] | None = None
        metadata_json = _build_completed_metadata_with_degraded_diarization(
            settings=settings,
            profile=job_context.profile,
            asr_result=asr_result,
            diarization_error_message=str(exc),
        )
    except Exception as exc:
        LOGGER.exception(
            "Diarization failed terminally for job id=%s requested_device=%s error_code=processing_error duration_ms=%s",
            job_id,
            job_context.requested_device,
            _elapsed_ms(diarization_started_at),
        )
        _mark_job_failed(
            session_factory=session_factory,
            job_id=job_id,
            error_code="processing_error",
            error_message=f"Worker processing failed: {exc}",
        )
        LOGGER.error(
            "Job id=%s finished with terminal failure error_code=processing_error duration_ms=%s",
            job_id,
            _elapsed_ms(job_started_at),
        )
        return
    else:
        speaker_segments_json = [
            dict(segment) for segment in diarization_result.speaker_segments_json
        ]
        metadata_json = _build_completed_metadata_with_diarization(
            settings=settings,
            profile=job_context.profile,
            asr_result=asr_result,
            diarization_result=diarization_result,
        )

    try:
        _persist_completed_job(
            session_factory=session_factory,
            job_id=job_id,
            transcript_text=asr_result.transcript_text,
            transcript_json=asr_result.transcript_json,
            speaker_segments_json=speaker_segments_json,
            detected_language=asr_result.detected_language,
            metadata_json=metadata_json,
        )
    except Exception as exc:
        LOGGER.exception(
            "Failed to persist completed result for job id=%s error_code=processing_error",
            job_id,
        )
        _mark_job_failed(
            session_factory=session_factory,
            job_id=job_id,
            error_code="processing_error",
            error_message=f"Failed to persist processing result: {exc}",
        )
        LOGGER.error(
            "Job id=%s finished with terminal failure error_code=processing_error duration_ms=%s",
            job_id,
            _elapsed_ms(job_started_at),
        )
        return

    if metadata_json["diarization_status"] == "completed":
        LOGGER.info(
            "Job id=%s finished successfully diarization_status=completed duration_ms=%s",
            job_id,
            _elapsed_ms(job_started_at),
        )
        return

    LOGGER.warning(
        "Job id=%s finished successfully with degraded diarization diarization_status=failed duration_ms=%s",
        job_id,
        _elapsed_ms(job_started_at),
    )


def run_worker_once(
    session_factory: sessionmaker[Session] | None = None,
    settings: Settings | None = None,
) -> bool:
    """Claim and process at most one pending job."""
    resolved_settings = settings or get_settings()
    resolved_session_factory = session_factory or create_session_factory()

    job_id = claim_next_pending_job(resolved_session_factory)
    if job_id is None:
        return False

    process_claimed_job(
        session_factory=resolved_session_factory,
        job_id=job_id,
        settings=resolved_settings,
    )
    return True


def run_worker_forever(
    session_factory: sessionmaker[Session] | None = None,
    settings: Settings | None = None,
) -> None:
    """Run the worker loop continuously using the configured poll interval."""
    resolved_settings = settings or get_settings()
    resolved_session_factory = session_factory or create_session_factory()
    processed_jobs_since_cleanup = 0
    reconciliation_interval = _get_reconciliation_interval_seconds(resolved_settings)
    last_reconciliation_at = perf_counter()
    cleanup_every_n_jobs = getattr(
        resolved_settings,
        "worker_cleanup_every_n_jobs",
        None,
    )

    while True:
        if perf_counter() - last_reconciliation_at >= reconciliation_interval:
            recovery_report = reconcile_stale_running_jobs(
                session_factory=resolved_session_factory,
                settings=resolved_settings,
                trigger="cadence",
            )
            log_reconciliation_report(
                logger=LOGGER,
                trigger="cadence",
                report=recovery_report,
            )
            last_reconciliation_at = perf_counter()

        processed_job = run_worker_once(
            session_factory=resolved_session_factory,
            settings=resolved_settings,
        )
        if processed_job:
            processed_jobs_since_cleanup += 1
            if (
                isinstance(cleanup_every_n_jobs, int)
                and cleanup_every_n_jobs >= 1
                and processed_jobs_since_cleanup >= cleanup_every_n_jobs
            ):
                cleanup_report = run_storage_cleanup(
                    session_factory=resolved_session_factory,
                    settings=resolved_settings,
                )
                log_storage_cleanup_report(
                    logger=LOGGER,
                    trigger="cadence",
                    report=cleanup_report,
                )
                processed_jobs_since_cleanup = 0
            continue

        if not processed_job:
            sleep(resolved_settings.worker_poll_interval_seconds)
