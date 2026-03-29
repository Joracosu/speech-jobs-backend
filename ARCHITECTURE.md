# ARCHITECTURE.md

## Purpose

This document is the technical architecture reference for `speech-jobs-backend`.

It explains the backend baseline, the main lifecycle, the current technical decisions, and the explicit limits of the implemented system. It is meant to support technical review and onboarding without duplicating the quickstart in `README.md` or the workflow guidance in `CONTRIBUTING.md`.

## At a glance

| area | responsibility | main files | notes |
| --- | --- | --- | --- |
| API | Validate uploads and expose the public read-side job contract | `src/app/main.py`, `src/app/api/routes/jobs.py`, `src/app/api/schemas/jobs.py` | Public surface includes health, upload, job status, and curated result retrieval |
| Worker | Claim jobs, run processing, persist outcomes, and handle bounded operational duties | `src/app/worker/main.py`, `src/app/worker/service.py`, `src/app/worker/runtime_checks.py` | Worker owns async execution, preflight, recovery, and cleanup triggers |
| Persistence | Store lifecycle state and persisted results separately | `src/app/db/models.py`, `src/app/api/routes/jobs.py` | `jobs` and `job_results` are distinct on purpose |
| Migrations | Version and apply schema changes | `alembic/versions/be857cdb2cd2_initial_schema.py`, `alembic/versions/2dbbb7d95cf8_add_job_heartbeat_tracking.py` | Alembic is part of the baseline, not an optional extra |
| Public result contract | Project persisted results to a stable external shape | `src/app/api/routes/jobs.py`, `src/app/api/schemas/jobs.py`, `tests/test_jobs_api.py` | Curated result retrieval intentionally hides raw internal metadata |
| Operational baseline | Keep the local backend reproducible and continuously checked | `docker-compose.yml`, `.github/workflows/ci.yml`, `tests/test_critical_flows.py` | CPU-first Compose path and CI both validate the current backend slice |

## Stable technical baseline

The supported baseline is **CPU-first** and local-first.

At the system level, that baseline consists of:

- a FastAPI API process
- a separate worker process
- persistent PostgreSQL storage
- Alembic-managed schema evolution
- local filesystem input/artifact storage
- reproducible local orchestration through `docker-compose.yml`
- CI validation that exercises migrations, tests, and app import

GPU execution exists as an optional path, and diarization exists as an optional path on top of the baseline processing story. They matter architecturally because the worker must be able to resolve runtime readiness and degrade safely, but they are not the universal baseline the repository depends on.

The worker preflight path is also part of the baseline architecture. It gives the system one bounded way to check ASR and diarization readiness without turning architecture documentation into a setup guide.

## Technical lifecycle

### Upload

The lifecycle starts with an audio upload to the API.

At this stage the system:

- validates format and viability of the uploaded audio
- stores the accepted input under local storage
- creates a persisted `pending` job row
- records profile and device-preference context with the job

The API stays intentionally light. It accepts the request, persists the minimum required state, and leaves heavy processing to the worker.

### Worker

The worker owns asynchronous execution.

Its architectural responsibilities are:

- polling for pending jobs
- claiming one job safely from the database
- moving the job into `running`
- executing ASR first and diarization second when appropriate
- persisting either a final result or a terminal failure

The worker-side processing path is intentionally layered:

- orchestration and lifecycle live in the worker service
- ASR and diarization stay behind narrower worker-side adapters
- runtime readiness is shared with the preflight path

This is also where the **silence short-circuit** belongs. When the input is confidently classified as real silence, the worker does not run the normal ASR and diarization path. Instead, it completes the job with a coherent empty result. That behavior protects the public contract from hallucinated transcript content while keeping the optimization internal to the worker boundary.

### Result retrieval

Public result retrieval is intentionally **curated**.

`GET /jobs/{job_id}/result` does not expose raw persistence records directly. Instead, it projects the persisted result into a smaller public shape with transcript data, language, speaker segments, and bounded diarization outcome fields.

The public outcome is explicit:

- `404` when the job does not exist
- `409` when the job exists but no public result is available yet
- `200` when a persisted `JobResult` exists

This **curated result retrieval** boundary matters because internal metadata remains useful for worker behavior and debugging, but it should not automatically become part of the public contract.

This section is also where the repository now owns **degraded diarization semantics**. If ASR succeeds and diarization fails in a controlled way, the backend still preserves the useful transcript, completes the job, and exposes the degraded outcome through the public result shape without leaking raw internal failure metadata.

### Recovery

Recovery is an explicit part of the architecture, not an afterthought.

The system now treats stale `running` jobs as recoverable lifecycle anomalies rather than letting them remain indefinitely ambiguous. That behavior is implemented through **stale-running reconciliation** backed by internal liveness tracking.

At the architecture level, the important point is:

- healthy in-flight jobs remain untouched
- stale `running` jobs without a persisted result are reconciled to terminal failure
- stale `running` jobs with a persisted result are reconciled to completion

The intent is to keep lifecycle state trustworthy after interruption without introducing retries, requeue systems, or new public states.

### Cleanup

Cleanup is a bounded worker responsibility.

The design intent is:

- clean up local input and optional artifact files by retention rules
- keep cleanup best-effort and local-only
- keep database lifecycle and persisted results as the source of truth

This means cleanup is deliberately narrower than a full retention platform. It removes expired local files when safe, but it does not redefine job history or turn cleanup into a scheduler or orchestration subsystem.

## Technical Decisions

### Dedicated worker + database-backed claim instead of in-request execution

Heavy processing stays outside the API request path, and the worker claims jobs through PostgreSQL-backed locking. This keeps HTTP behavior predictable and makes concurrency control part of the persisted lifecycle rather than an in-memory convention.

### PostgreSQL + SQLAlchemy + Alembic are the persistence backbone, not a temporary placeholder

The repository already treats persistent state, schema evolution, and lifecycle coordination as first-class concerns. PostgreSQL and Alembic are enough for the current backend slice, so queue infrastructure such as Redis, RabbitMQ, or Celery remains intentionally out of scope.

### Lifecycle state and final result payload are separated on purpose

`jobs` owns lifecycle and execution context. `job_results` owns final payload worth persisting. This makes public result retrieval easier to reason about and avoids one wide table that mixes claim state, execution errors, and transcript payloads.

### The public result is curated instead of mirroring raw persistence

The repository exposes a stable external result contract, not a dump of internal runtime metadata. That decision keeps public API semantics smaller and lets internal metadata evolve without becoming accidental surface area.

### The baseline is transcription-first, with bounded optional diarization

The backend is still valuable when transcription succeeds even if diarization is unavailable or degrades. That is why **degraded diarization semantics** are explicit and why the repository preserves useful ASR output instead of treating every downstream diarization problem as a full product failure.

### Silence and interruption are handled as first-class lifecycle behaviors

The architecture now owns two important bounded behaviors:

- **silence short-circuit** to avoid producing meaningless transcript text for real silence
- **stale-running reconciliation** to prevent interrupted jobs from remaining indefinitely in `running`

Both behaviors improve lifecycle integrity without opening broader infrastructure tracks.

### Operational checks stay close to real worker behavior

The preflight path, recovery behavior, and cleanup behavior all stay within the worker-owned operational boundary. The goal is not to build a large observability or orchestration stack, but to make the current backend slice technically honest, diagnosable, and reproducible.

## Out of scope / not part of this backend baseline

The current architecture does **not** try to be:

- a multi-user platform
- an authentication or authorization system
- a distributed queue platform
- a cloud deployment template
- a frontend application
- a speaker-identity product
- an advanced observability platform
- a retry-heavy orchestration system

Those exclusions are part of the architecture story. They keep the repository focused on lifecycle clarity, persistence discipline, bounded audio processing, and a publishable backend baseline.
