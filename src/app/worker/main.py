"""Command-line entrypoint for the local dedicated worker process."""

from __future__ import annotations

import argparse
import logging
from collections.abc import Sequence

from app.core.settings import get_settings
from app.worker.runtime_checks import (
    SUPPORTED_DEVICE_PREFERENCES,
    format_worker_runtime_report,
    inspect_worker_runtime,
)


LOGGER = logging.getLogger(__name__)


def _configure_worker_logging() -> None:
    """Install a small default logging configuration for the worker CLI."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
        force=True,
    )


def _run_worker_execution(args: argparse.Namespace, settings: object) -> int:
    """Run the non-preflight worker path with deferred heavy imports."""
    from app.db.config import create_session_factory
    from app.worker.cleanup import log_storage_cleanup_report, run_storage_cleanup
    from app.worker.service import (
        log_reconciliation_report,
        reconcile_stale_running_jobs,
        run_worker_forever,
        run_worker_once,
    )

    session_factory = create_session_factory()
    recovery_report = reconcile_stale_running_jobs(
        session_factory=session_factory,
        settings=settings,
        trigger="startup",
    )
    log_reconciliation_report(
        logger=LOGGER,
        trigger="startup",
        report=recovery_report,
    )

    cleanup_report = run_storage_cleanup(
        session_factory=session_factory,
        settings=settings,
    )
    log_storage_cleanup_report(
        logger=LOGGER,
        trigger="startup",
        report=cleanup_report,
    )

    if args.once:
        run_worker_once(session_factory=session_factory, settings=settings)
        return 0

    run_worker_forever(session_factory=session_factory, settings=settings)
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """Run the worker once or forever, depending on CLI arguments."""
    parser = argparse.ArgumentParser(
        prog="python -m app.worker.main",
        description="Run the local speech-jobs-backend worker.",
    )
    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument(
        "--once",
        action="store_true",
        help="Process at most one pending job and then exit.",
    )
    mode_group.add_argument(
        "--preflight",
        action="store_true",
        help="Check worker runtime readiness without processing any jobs.",
    )
    parser.add_argument(
        "--device",
        choices=sorted(SUPPORTED_DEVICE_PREFERENCES),
        help="Override the device path validated by --preflight.",
    )
    args = parser.parse_args(argv)

    if args.device and not args.preflight:
        parser.error("--device can only be used together with --preflight.")

    _configure_worker_logging()

    settings = get_settings()
    if args.preflight:
        requested_device = args.device or settings.device_preference
        runtime_report = inspect_worker_runtime(
            requested_device=requested_device,
            model_id=settings.diarization_model_id,
            huggingface_token=settings.huggingface_token,
        )
        print(format_worker_runtime_report(runtime_report))
        if runtime_report.ready:
            LOGGER.info("Worker runtime preflight completed successfully.")
            return 0

        LOGGER.warning("Worker runtime preflight reported missing readiness.")
        return 1

    return _run_worker_execution(args, settings)


if __name__ == "__main__":
    raise SystemExit(main())
