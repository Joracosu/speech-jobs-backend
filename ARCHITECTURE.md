# ARCHITECTURE.md

## Purpose

This document defines the technical architecture of **speech-jobs-backend**.

It explains the main system components, their responsibilities, the data flow between them, and the implementation boundaries for v1.

This is a **living document** and must be updated when architectural decisions change.

---

## Architecture Overview

The application is designed as a **backend-first asynchronous processing system** for audio transcription and speaker diarization.

The system is intentionally structured around a small number of explicit components:

- **HTTP API** for job creation and read-side job access
- **Database** for persistent job state and results
- **Worker process** for heavy processing
- **Local storage** for uploaded audio files and optional generated artifacts
- **Speech processing pipeline** for transcription and diarization

The main architectural goal is to demonstrate clear backend system design rather than to maximize speech model sophistication.

---

## Technical Decisions

These decisions capture backend choices already materialized in the current repository or directly governing its immediate evolution.

### 1. Dedicated worker instead of in-process/background execution

- Context: the repository already exposes upload and job-query APIs, while processing must remain asynchronous and observable.
- Decision: keep heavy job execution in a dedicated worker process, separate from the FastAPI request process.
- Why this choice: it keeps HTTP latency predictable, makes lifecycle handling explicit, and preserves a clean API/worker split.
- Trade-offs / what we are not doing: no in-process background tasks and no API-side execution shortcuts, even for placeholder processing.
- Interview defense: this keeps the system readable and production-shaped early. The separation is useful before model complexity arrives, not after.

### 2. DB-backed claim with PostgreSQL locking instead of naive polling/claiming

- Context: pending jobs are persisted in PostgreSQL and the worker must avoid accidental double processing.
- Decision: claim jobs through a database transaction using PostgreSQL row locking (`FOR UPDATE SKIP LOCKED`).
- Why this choice: the database is already the source of truth for lifecycle state, so claim safety belongs there too.
- Trade-offs / what we are not doing: no naive "read pending then update later" flow, and no ad hoc in-memory coordination between worker loops.
- Interview defense: this is the smallest correct concurrency boundary for the current system. It is local-friendly now and still compatible with future multi-worker expansion.

### 3. PostgreSQL + SQLAlchemy + Alembic are enough at this stage; no queue infrastructure yet

- Context: the current backend already persists jobs and results, versions schema changes, and runs a dedicated worker process.
- Decision: use PostgreSQL, SQLAlchemy 2.x, and Alembic as the persistence backbone without adding Redis, RabbitMQ, or Celery yet.
- Why this choice: these tools already cover durable state, schema evolution, lifecycle tracking, and worker coordination for the current slice.
- Trade-offs / what we are not doing: no queue broker, no distributed orchestration layer, and no extra infrastructure before real scaling pressure exists.
- Interview defense: this keeps the stack proportional to the implemented requirements. The repository demonstrates persistence and lifecycle discipline without hiding the core design behind premature infrastructure.

### 4. Small explicit API surface instead of broad feature expansion

- Context: the current public backend slice is intentionally limited to health, read-side job access, and upload-driven job creation.
- Decision: keep the API small and explicit until the core lifecycle and processing path are stable.
- Why this choice: it keeps contracts easy to reason about and reduces accidental scope creep while the system foundations are still taking shape.
- Trade-offs / what we are not doing: no retries, cancellation, bulk flows, artifact downloads, or result endpoint before the underlying behavior is ready.
- Interview defense: a narrow API is a deliberate quality decision here. It lets the backend prove the important path first instead of scattering effort across partial features.

### 5. Worker/lifecycle first, then speech-model integration

- Context: the repository now has a completed worker/lifecycle foundation and has already integrated real ASR and internal diarization on top of it.
- Decision: the project established upload, persistence, claim logic, lifecycle transitions, and result persistence first, and only then integrated speech models into the worker.
- Why this choice: it isolated infrastructure and state-management problems from model-integration problems, which made ASR and diarization easier to add cleanly.
- Trade-offs / what we are not doing: result retrieval was exposed only after the worker/result semantics were stable, and the repository still does not try to integrate every speech-facing concern at once.
- Interview defense: this sequence reduced risk and kept the backend story coherent. The current repo shows that the worker foundation was solved before model complexity was added.

### 6. Transcription-first baseline remains valid if diarization is postponed

- Context: diarization is useful for the intended v1, but it is also the most likely feature to become disproportionately costly or unstable.
- Decision: keep a transcription-capable backend as a valid publishable baseline even if diarization lands later than planned.
- Why this choice: it protects delivery quality and lets the repository remain professionally defensible without overpromising on the hardest part.
- Trade-offs / what we are not doing: no forced all-or-nothing delivery where diarization delays make the whole backend look incomplete.
- Interview defense: this is controlled scope management, not feature retreat. It preserves a strong backend story while acknowledging the real cost profile of diarization.

### 7. Explicit v1 exclusions as a scope-control choice

- Context: the project is meant to read as a focused backend portfolio piece, not as a broad product platform.
- Decision: keep auth, cloud deployment, advanced observability, external queueing, and other side tracks explicitly out of v1.
- Why this choice: it protects architecture clarity and keeps effort concentrated on lifecycle, persistence, and processing behavior.
- Trade-offs / what we are not doing: no multi-user platform work, no cloud expansion, and no infrastructure polish that does not strengthen the core backend signal.
- Interview defense: saying "no" is part of backend design quality. The exclusions make the repository more coherent and easier to justify in review.

### 8. Runtime readiness checks stay shared between preflight and worker adapters

- Context: ASR and diarization now run through different native/runtime stacks, and recent manual validation showed that one can be ready while the other is not.
- Decision: keep runtime device resolution and readiness checks behind a small shared worker module reused by the CLI preflight and by the ASR/diarization adapters.
- Why this choice: it keeps diagnostics consistent with real worker behavior and avoids a parallel preflight implementation that can drift from production execution.
- Trade-offs / what we are not doing: no public readiness endpoint, but the worker preflight is allowed to perform a lightweight Hugging Face access check against the configured diarization model so local readiness matches real execution more closely.
- Interview defense: this is a backend ownership choice. The same runtime truth should drive diagnostics, execution, and tests.

---

## Core Components

### 1. API Layer

**Technology:** FastAPI

The API layer is responsible for:

- receiving uploaded audio files
- validating incoming requests
- storing accepted inputs
- creating processing jobs in the database
- exposing job status and read-side job endpoints
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

**Current implementation:** real ASR transcription with `faster-whisper` plus internal speaker diarization with `pyannote.audio`

The processing layer is responsible for:

- loading the stored audio input
- running the current ASR transcription flow
- running diarization after successful ASR
- assembling transcript and speaker segmentation as parallel persisted artifacts
- collecting processing metadata
- exposing a worker-side preflight path that checks ASR and diarization runtime readiness without processing a job

The current result model persists transcript artifacts plus `speaker_segments_json`. Successful diarization persists a JSON list, while controlled diarization failure after valid ASR preserves the transcript and stores `speaker_segments_json = None` with internal metadata describing the degraded outcome. The API now exposes a curated public result projection through `GET /jobs/{job_id}/result`, without exposing raw `metadata_json`, and this step still does not add transcript-speaker alignment heuristics.

ASR and diarization readiness are checked separately because they use different runtime stacks. The worker preflight reuses the same device-resolution and capability-check logic as the adapters, so runtime diagnostics stay aligned with actual job execution. The diarization adapter also preloads audio explicitly before calling `pyannote.audio`, which avoids depending on `torchcodec`-based local file decoding during worker execution on the current Windows/CUDA target.

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
5. The worker runs ASR transcription.
6. If ASR fails or the input is broken, the worker marks the job as `failed`.
7. If ASR succeeds, the worker attempts speaker diarization.
8. If diarization succeeds, the worker stores transcript plus speaker-segment artifacts in `job_results` and marks the job as `completed`.
9. If diarization fails in a controlled way after valid ASR, the worker still stores the transcript in `job_results`, keeps `speaker_segments_json = None`, records the degraded diarization outcome in metadata, and marks the job as `completed`.
10. Unexpected processing or persistence failures remain terminal and are stored as `failed`.

### Retrieval

1. A client requests job status through the API.
2. The API reads the job record from the database.
3. Completed jobs may already have a persisted internal result in `job_results`.
4. `GET /jobs/{job_id}/result` returns `404` when the job does not exist, `409` when no public result is available yet, and `200` when a persisted `JobResult` exists.
5. The public result is a curated projection: transcript text, curated transcript structure, speaker segments, language, and limited diarization outcome fields are exposed, while raw `metadata_json` and internal runtime fields remain internal.

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

### Preflight mode

The worker now exposes a CLI-only preflight mode:

- `python -m app.worker.main --preflight`
- `python -m app.worker.main --preflight --device auto|cpu|cuda`

This mode must:

- run before any job claim or DB session creation
- report ASR and diarization readiness separately
- report the requested and resolved device path per component
- return a nonzero exit code when the selected runtime path is not ready

Global `READY` means both ASR and diarization are ready for the selected device path. For `cpu`, missing CUDA is not blocking. For `cuda`, both runtimes must be CUDA-ready. For `auto`, each component resolves its effective device using the same logic as real worker execution.

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

At the current stage, `job_results` persists transcript output and speaker segments internally, and the API exposes a curated public result projection from that data.
At the current stage, completed results may also represent degraded diarization outcomes internally: transcript is still persisted, while `speaker_segments_json` is `None` and metadata records that diarization was attempted but failed. Public result retrieval preserves that distinction as `speaker_segments_json = null` without exposing raw internal error metadata.

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
- `missing_input_file`
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
- log runtime resolution for ASR and diarization when processing starts
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
