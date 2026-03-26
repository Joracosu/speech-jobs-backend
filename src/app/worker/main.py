"""Command-line entrypoint for the local dedicated worker process."""

from __future__ import annotations

import argparse

from app.core.settings import get_settings
from app.db.config import create_session_factory
from app.worker.service import run_worker_forever, run_worker_once


def main() -> None:
    """Run the worker once or forever, depending on CLI arguments."""
    parser = argparse.ArgumentParser(
        prog="python -m app.worker.main",
        description="Run the local speech-jobs-backend worker.",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Process at most one pending job and then exit.",
    )
    args = parser.parse_args()

    settings = get_settings()
    session_factory = create_session_factory()

    if args.once:
        run_worker_once(session_factory=session_factory, settings=settings)
        return

    run_worker_forever(session_factory=session_factory, settings=settings)


if __name__ == "__main__":
    main()
