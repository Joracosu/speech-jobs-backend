# ARCHITECTURE.md

## Purpose

This document defines the technical architecture of **speech-jobs-backend**.

It explains the main system components, their responsibilities, the data flow between them, and the implementation boundaries for v1.

This is a **living document** and must be updated when architectural decisions change.

---

## Architecture Overview

The application is designed as a **backend-first asynchronous processing system** for audio transcription and speaker diarization.

The system is intentionally structured around a small number of explicit components:

- **HTTP API** for job creation and result retrieval
- **Database** for persistent job state and results
- **Worker process** for heavy processing
- **Local storage** for uploaded audio files and optional generated artifacts
- **Speech processing pipeline** for transcription and diarization

The main architectural goal is to demonstrate clear backend system design rather than to maximize speech model sophistication.

---

## Core Components

### 1. API Layer

**Technology:** FastAPI

The API layer is responsible for:

- receiving uploaded audio files
- validating incoming requests
- storing accepted inputs
- creating processing jobs in the database
- exposing job status and job result endpoints
- returning clear error responses

The API must remain lightweight and must not perform heavy speech processing directly.

### 2. Database Layer

**Technology:** PostgreSQL + SQLAlchemy 2.x + Alembic

The database is the source of truth for:

- job lifecycle state
- file metadata
- configuration snapshot per job
- processing errors
- final structured results
- audit-oriented metadata

Database schema changes must be managed through Alembic migrations.

### 3. Worker Layer

**Technology:** Dedicated Python worker process

The worker is responsible for:

- polling for pending jobs
- claiming a job safely
- updating job state to `running`
- executing the processing pipeline
- storing results
- marking jobs as `completed` or `failed`
- performing lightweight cleanup tasks when appropriate

The worker is separate from the API process by design.

### 4. Storage Layer

**Technology:** local filesystem storage in v1

The storage layer is responsible for:

- persisting uploaded audio files
- optionally keeping generated artifacts
- applying retention rules through TTL-based cleanup

Storage is intentionally local in v1 to keep the system simple and reproducible.

### 5. Processing Layer

**Technology:** `faster-whisper` + `pyannote.audio`

The processing layer is responsible for:

- loading the stored audio input
- running transcription
- running speaker diarization
- assembling the final structured result
- collecting processing metadata

The processing layer must be isolated from the API layer and remain callable from the worker only.

---

## End-to-End Flow

### Upload and job creation

1. A client uploads an audio file to the API.
2. The API validates the request.
3. The API stores the uploaded file in local storage.
4. The API creates a new database record in `jobs` with status `pending`.
5. The API returns the new `job_id` to the client.

### Background processing

1. The worker polls for pending jobs.
2. The worker claims one job atomically.
3. The worker updates the job to `running`.
4. The worker loads the file from storage.
5. The worker runs transcription.
6. The worker runs speaker diarization.
7. The worker builds the final result payload.
8. The worker stores the result in `job_results`.
9. The worker marks the job as `completed`.
10. If a failure occurs, the worker marks the job as `failed` and stores error details.

### Retrieval

1. A client requests job status through the API.
2. The API reads the job record from the database.
3. If requested, the API returns the persisted final result.

---

## Public API Scope for v1

The initial API surface is intentionally small.

### Planned endpoints

- `GET /health`
- `POST /jobs/upload`
- `GET /jobs`
- `GET /jobs/{job_id}`
- `GET /jobs/{job_id}/result`

### Not included in v1

- job cancellation
- manual retries
- artifact download endpoints
- bulk uploads
- authentication and authorization
- user accounts
- distributed queue management

---

## Worker Strategy

### Polling model

The worker uses a polling-based design in v1.

This is acceptable because:

- the project is local-first
- the system is not distributed in v1
- the focus is on architecture clarity and persistence
- it avoids adding Redis or RabbitMQ too early

### Claim model

The worker must claim jobs safely using a database-backed claim operation.

The claim flow should ensure:

- only jobs in `pending` state can be claimed
- state transition to `running` is atomic
- duplicate processing is avoided
- the claim mechanism remains compatible with future multi-worker expansion

### Visible lifecycle states

Public job states are limited to:

- `pending`
- `running`
- `completed`
- `failed`

Internal claim details may be recorded as metadata but should not become extra public lifecycle states in v1.

---

## Data Model Summary

### `jobs`

The `jobs` table is responsible for job lifecycle and execution context.

Planned fields include:

- `id`
- `status`
- `created_at`
- `started_at`
- `completed_at`
- `updated_at`
- `original_filename`
- `stored_path`
- `input_sha256`
- `file_size_bytes`
- `media_duration_seconds` (nullable)
- `device_used`
- `profile_selected`
- `config_snapshot`
- `error_code` (nullable)
- `error_message` (nullable)

### `job_results`

The `job_results` table is responsible for the final output payload.

Planned fields include:

- `id`
- `job_id`
- `transcript_text`
- `transcript_json`
- `speaker_segments_json`
- `detected_language` (nullable)
- `metadata_json`

### Rationale for separate tables

The split between `jobs` and `job_results` is intentional.

It keeps:

- lifecycle state separate from output payload
- failure handling cleaner
- future evolution easier
- database design more explicit

---

## Storage and Retention Policy

### Input storage

Uploaded audio files are stored locally in v1.

### Artifact policy

Intermediate artifacts are optional and are not a required part of the public API.

### Retention model

Retention is TTL-based and configurable.

Initial intended defaults:

- input audio retention: **7 days**
- intermediate artifact retention: **7 days**
- final database result retention: **persistent**

### Cleanup strategy

Cleanup should be lightweight.

In v1 it is acceptable for cleanup logic to run:

- at worker startup
- and/or every _N_ processed jobs

A full scheduler is intentionally out of scope for v1.

---

## Audio Input Validation

Audio validation must include all of the following:

- file extension allowlist
- content type as a soft validation signal
- actual load/decode attempt before processing

### Supported formats for v1

Initial allowlist:

- `m4a`
- `mp3`
- `wav`
- `flac`
- `ogg`
- `opus`

The system should identify format viability automatically on the backend. The user should not need to manually specify an audio format.

---

## Processing Profiles

The public API exposes simplified profiles instead of raw model names.

### Planned profiles

- `fast`
- `balanced`
- `accurate`

### Device preference

The public API also supports:

- `auto`
- `cpu`
- `cuda`

### Design rationale

This keeps the public interface:

- simpler
- more stable over time
- less coupled to specific underlying model names

The exact internal model mapping may evolve as implementation is refined.

The default profile should be **`balanced`**.

---

## Error Model

The system should expose clear, limited, predictable error categories.

### Planned error codes

- `unsupported_format`
- `storage_error`
- `processing_error`
- `diarization_error`
- `internal_error`

Each failed job should persist:

- a machine-readable `error_code`
- a human-readable `error_message`

---

## Logging and Traceability

Even in v1, the application must not be operationally blind.

Minimum expected logging behavior:

- log job creation
- log worker claim/start
- log processing completion
- log failures with job context
- include `job_id` in relevant log lines
- record processing duration where possible

This project does **not** require a full observability stack in v1.

---

## Configuration Strategy

Configuration must be explicit and environment-based.

Likely configuration categories include:

- database connection
- storage paths
- retention values
- processing defaults
- Hugging Face token
- device preference defaults

A public `.env.example` file should document the required environment variables without exposing secrets.

---

## Code Structure Direction

The application code is expected to evolve toward this structure:

```text
src/app/
  api/
  core/
  db/
  models/
  repositories/
  services/
  processing/
  worker/
tests/
alembic/
storage/
runs/   # ignored
```

This structure is meant to be modular but not excessively fragmented.

---

## Deliberate Exclusions for v1

The following are intentionally excluded from the initial architecture:

- Celery or distributed queues
- cloud object storage
- frontend UI
- authentication and authorization
- multi-user workflows
- speaker identity recognition
- cancellation workflows
- advanced retries and scheduling
- advanced monitoring stack

These exclusions are deliberate and aligned with the portfolio goal of the project.

---

## Main Architectural Risks

### 1. Diarization becomes a technical sink

Mitigation:

- keep the public promise limited
- avoid pursuing perfection
- treat diarization as useful but bounded

### 2. Documentation grows faster than the product

Mitigation:

- keep each document scoped to a single purpose
- avoid duplication across project files
- review documentation at Quality Gates

### 3. The execution plan becomes too rigid

Mitigation:

- use the plan as guidance, not as a prison
- allow controlled adaptation while preserving structure

### 4. The repository stops telling a coherent story

Mitigation:

- keep commits small and meaningful
- maintain a clear README and architecture narrative
- use milestones and delivery logging consistently

---

## Architectural Priorities

The project prioritizes architecture in this order:

1. architecture quality
2. reproducibility
3. clarity of contracts and data flow
4. stable job execution lifecycle
5. output usefulness

This order must guide implementation decisions when trade-offs appear.
