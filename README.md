# speech-jobs-backend

Asynchronous backend service for audio processing, focused on transcription and speaker diarization through a job-based API.

This project is being built as a professional backend portfolio piece. The main goal is to demonstrate system design, asynchronous processing, persistence, reproducibility, and clear engineering decisions rather than chasing the most advanced speech quality possible.

## Status

Active development.

The repository now includes an executable FastAPI baseline with `GET /health`, `GET /jobs`, `GET /jobs/{job_id}`, `GET /jobs/{job_id}/result`, and `POST /jobs/upload`, plus centralized settings, lazy database bootstrap, initial ORM models (`Job`, `JobResult`), Alembic integration, an initial migration applied to PostgreSQL, and a dedicated worker that runs real ASR transcription with `faster-whisper` plus internal speaker diarization with `pyannote.audio`, persisting transcript and speaker segments in `JobResult`. The worker also exposes a CLI preflight to verify runtime readiness before real jobs run.

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

## Worker Runtime Preflight

Use the worker CLI to verify runtime readiness before processing real jobs:

- `python -m app.worker.main --preflight`
- `python -m app.worker.main --preflight --device cpu`
- `python -m app.worker.main --preflight --device cuda`

The preflight checks ASR and diarization separately and reports a global `READY` state only when both components are ready for the selected device path. This matters because ASR uses the `ctranslate2` / `faster-whisper` stack, while diarization depends on `torch` / `pyannote.audio` plus the required Hugging Face token.

For the current Windows/CUDA target, `requirements.txt` now pins the PyTorch CUDA wheel family through the PyTorch `cu128` index so the local worker can run ASR and diarization in the same `.venv` without a manual post-install torch step.

The diarization preflight also validates that the configured `DIARIZATION_MODEL_ID` is actually accessible with the current `HUGGINGFACE_TOKEN`. For `pyannote/speaker-diarization-community-1`, that token must belong to an account that has already accepted the model access conditions on Hugging Face.

When diarization succeeds, `speaker_segments_json` is persisted as a JSON list, including `[]` when normalization yields no valid segments. When ASR succeeds but diarization fails in a controlled way, the worker now still preserves the transcript, marks the job as `completed`, stores `speaker_segments_json = None`, and records the degraded diarization outcome in internal result metadata.

## Public Result Retrieval

Use `GET /jobs/{job_id}/result` to read the curated public result for one persisted job.

- `200` when a `JobResult` exists
- `404` when the job does not exist
- `409` when the job exists but no public result is available yet

The public result payload exposes:

- `job_id`
- `transcript_text`
- `transcript_json` with only `segments` and `language`
- `speaker_segments_json`
- `detected_language`
- `empty_transcript`
- `diarization_attempted`
- `diarization_status`

The API does not expose raw `metadata_json` or internal runtime fields such as `worker_id`, `requested_device`, `resolved_device`, `compute_type`, `engine`, or `model`.

Successful diarization keeps `speaker_segments_json` as a JSON list, including `[]` when normalization yields no valid segments. Controlled diarization degradation keeps `speaker_segments_json = null` while still returning the preserved transcript.

Storage retention and cleanup remain separate work and are not implemented by this endpoint.

## Demo Audio Sources

- `conversation_two_speakers_10m.m4a`: excerpt from "Colm Walsh on holy wells and other places around Graiguenamanagh", source Wikimedia Commons, author A.-K. D., license CC0 1.0
- `monologue_james_6m20s.m4a`: source recording "James speaking West Riding Yorkshire English", source Wikimedia Commons / Wikitongues, author Wikitongues Inc, license CC0 1.0
