# speech-jobs-backend

Asynchronous backend service for audio processing, focused on transcription and speaker diarization through a job-based API.

This project is being built as a professional backend portfolio piece. The main goal is to demonstrate system design, asynchronous processing, persistence, reproducibility, and clear engineering decisions rather than chasing the most advanced speech quality possible.

## Status

Early development.

The repository now includes an executable FastAPI baseline with `GET /health` and one basic automated test.

## v1 Goals

Version 1 is planned to include:

- Audio upload through an HTTP API
- Job creation and status tracking
- Asynchronous processing through a dedicated worker
- Speech transcription
- Speaker diarization
- Persistent storage of job state and processing results
- Local development with Docker Compose
- Database schema management with migrations
- Basic tests and validation workflow
- Minimal visible CI/CD for install, tests, and basic app verification

## Architecture Direction

The initial architecture is intentionally backend-first:

- **FastAPI** for the HTTP API
- **PostgreSQL** for persistence
- **SQLAlchemy 2.x** for database access
- **Alembic** for migrations
- **Dedicated worker process** for heavy background processing
- **faster-whisper** for transcription
- **pyannote.audio** for diarization

## Design Priorities

This project prioritizes:

1. Architecture quality
2. Reproducibility
3. Clarity of data flow and API design
4. Stable asynchronous job processing
5. Useful transcription and diarization results

## Out of Scope for v1

The following are intentionally excluded from the first version:

- Multi-user support
- Authentication and authorization
- Cloud deployment
- Distributed task queues such as Celery
- Speaker identification by real identity
- Advanced observability stack
- Frontend application

These may be considered in future iterations, but they are not part of the initial scope.

## Repository Structure

The repository will progressively include:

- project governance documents
- execution planning documents
- Python project metadata
- application source code
- tests
- migration files
- local storage support for uploaded media and generated artifacts

## Notes

This repository follows an incremental development approach with small, stable, reviewable steps.

Implementation details, coding rules, architectural decisions, milestones, and execution planning are documented in dedicated project files inside the repository.
