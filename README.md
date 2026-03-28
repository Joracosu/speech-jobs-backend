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

The worker preflight is the recommended way to verify real runtime readiness for the local environment.

Base runtime requirements:

- reachable PostgreSQL database with the expected schema/migrations applied
- local worker environment installed and able to import the configured runtime stack

Optional GPU path:

- only required when `cuda` is selected
- needs a compatible local CUDA-enabled runtime for the ASR and diarization stacks you expect to use

Optional diarization-enabled path:

- only required when full diarization execution is expected
- needs a valid `HUGGINGFACE_TOKEN`
- gated diarization models or repositories may require access to be accepted in the relevant Hugging Face account before the token works

The preflight checks ASR and diarization separately and reports a global `READY` state only when both components are ready for the selected device path. This matters because ASR uses the `ctranslate2` / `faster-whisper` stack, while diarization depends on `torch` / `pyannote.audio` plus the required Hugging Face token.

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

## Local Storage Cleanup

`S18` now implements lightweight TTL-based cleanup for local storage only.

- uploaded input files under `input_storage_dir`
- optional local artifacts under `artifact_storage_dir`
- database rows in `jobs` and `job_results` remain persistent

Cleanup is worker-driven, not scheduler-driven:

- one best-effort cleanup pass runs when the worker starts in the non-preflight path
- in long-running worker mode, another best-effort cleanup pass runs every `worker_cleanup_every_n_jobs` processed jobs when that setting is `>= 1`
- if `worker_cleanup_every_n_jobs` is `0`, negative, or `None`, periodic cleanup is disabled without error

Input cleanup is conservative and DB-aware. A physical input file is deleted only when every job that references the same `stored_path` is already terminal (`completed` or `failed`) and expired by TTL. Active jobs and shared paths that still have a non-expired terminal reference are preserved. Cleanup only removes local files, never database results.

Blank or missing `stored_path` values are skipped safely and silently during cleanup. They are treated as anomalous legacy data for retention purposes, not as actionable path-safety failures.

Artifact cleanup stays local and filesystem-based. It deletes expired files under `artifact_storage_dir`, prunes empty directories when safe, and remains a no-op when artifact storage is disabled or absent.

`INPUT_RETENTION_DAYS=None` or `ARTIFACT_RETENTION_DAYS=None` disables cleanup for that category in a safe, non-deleting way and may surface as a lightweight warning/report entry. Negative values also disable cleanup for that category.

Relevant retention settings and defaults:

- `INPUT_RETENTION_DAYS=7`
- `ARTIFACT_RETENTION_DAYS=7`
- `WORKER_CLEANUP_EVERY_N_JOBS=10`
- `STORE_INTERMEDIATE_ARTIFACTS=false`

## Operational Diagnostics

`S19` now adds minimal operational logging without changing API or worker semantics.

- the API emits compact domain logs for accepted uploads and `GET /jobs/{job_id}/result` outcomes (`200`, `404`, `409`)
- the worker logs claim/start, ASR, diarization, full success, degraded success, and terminal failure with `job_id`
- cleanup passes now log one compact summary with counters, warning count, trigger, and `duration_ms`

Timings use `duration_ms` consistently:

- upload handler timing is measured locally inside the upload endpoint
- result retrieval timing is measured locally inside the result handler
- worker logs include total job timing plus ASR and diarization phase timing
- cleanup timing covers the whole cleanup pass

The logs are intentionally compact and avoid leaking transcript text, full transcript JSON, raw metadata payloads, or an advanced observability stack.

## Demo Audio Sources

- `conversation_two_speakers_10m.m4a`: excerpt from "Colm Walsh on holy wells and other places around Graiguenamanagh", source Wikimedia Commons, author A.-K. D., license CC0 1.0
- `monologue_james_6m20s.m4a`: source recording "James speaking West Riding Yorkshire English", source Wikimedia Commons / Wikitongues, author Wikitongues Inc, license CC0 1.0
