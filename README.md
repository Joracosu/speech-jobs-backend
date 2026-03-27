# speech-jobs-backend

Asynchronous backend service for audio processing, focused on transcription and speaker diarization through a job-based API.

This project is being built as a professional backend portfolio piece. The main goal is to demonstrate system design, asynchronous processing, persistence, reproducibility, and clear engineering decisions rather than chasing the most advanced speech quality possible.

## Status

Active development.

The repository now includes an executable FastAPI baseline with `GET /health`, `GET /jobs`, `GET /jobs/{job_id}`, and `POST /jobs/upload`, plus centralized settings, lazy database bootstrap, initial ORM models (`Job`, `JobResult`), Alembic integration, an initial migration applied to PostgreSQL, and a dedicated worker that runs real ASR transcription with `faster-whisper` and persists transcription output in `JobResult`. Diarization remains intentionally out of current scope, and there is still no public result retrieval endpoint yet.

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

## Demo Audio Sources

- `conversation_two_speakers_10m.m4a`: excerpt from "Colm Walsh on holy wells and other places around Graiguenamanagh", source Wikimedia Commons, author A.-K. D., license CC0 1.0
- `monologue_james_6m20s.m4a`: source recording "James speaking West Riding Yorkshire English", source Wikimedia Commons / Wikitongues, author Wikitongues Inc, license CC0 1.0
