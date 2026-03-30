# App

## What this folder contains

This folder contains the executable backend package for the repository.

- [`main.py`](main.py) creates the FastAPI application and wires the public routers.
- [`api/`](api/) holds the HTTP request layer, public schemas, and request-scoped dependencies.
- [`core/`](core/) centralizes runtime settings and shared configuration.
- [`db/`](db/) owns SQLAlchemy base/config wiring and the persisted job models.
- [`services/`](services/) holds narrower backend helpers such as upload validation and local input storage.
- [`worker/`](worker/) owns asynchronous processing and has its own localized README.

## Why it exists

`src/app/` groups the backend runtime pieces in one place so readers can follow the code from application bootstrap into request handling, persistence, upload/storage helpers, and worker execution.

This README exists as a package map for onboarding into that layout. The root docs stay responsible for quickstart, system baseline, and contribution workflow, while this file shows where those concerns land in code.

## Key files to read first

- [`main.py`](main.py): start here for the FastAPI bootstrap and router wiring.
- [`core/settings.py`](core/settings.py): read this for the shared runtime configuration surface.
- [`db/models.py`](db/models.py): use this to understand the persisted job and job-result shapes.
- [`services/uploads.py`](services/uploads.py): read this for upload validation and local input-storage handling.
- [`api/routes/jobs.py`](api/routes/jobs.py): use this as the main public request-path anchor.
- [`worker/README.md`](worker/README.md): continue here for the worker-owned processing path.

## What should not live here

- Quickstart and repository-level navigation belong to [`README.md`](../../README.md).
- The full technical baseline and lifecycle explanation belong to [`ARCHITECTURE.md`](../../ARCHITECTURE.md).
- Contribution workflow and validation baseline belong to [`CONTRIBUTING.md`](../../CONTRIBUTING.md).
- The third-party inventory belongs to [`THIRD_PARTY.md`](../../THIRD_PARTY.md).
- This package map should not turn into a second root README or a dump of unrelated helpers without clear package ownership.

## How this area is tested / validated

Relevant validation here stays at the package-map level:

- Public API bootstrap and read-side checks: `tests/test_health.py`, `tests/test_job_upload_api.py`, and `tests/test_jobs_api.py`.
- Cross-boundary backend flow coverage: `tests/test_critical_flows.py`.
- Worker-owned processing depth is documented separately in [`worker/README.md`](worker/README.md) and the worker-focused tests it points to.

## Related docs

- [Repository overview](../../README.md)
- [Technical baseline and lifecycle](../../ARCHITECTURE.md)
- [Contribution workflow and validation baseline](../../CONTRIBUTING.md)
- Backend package docs:
  - [API layer overview](api/README.md)
  - [DB layer overview](db/README.md)
  - [Services layer overview](services/README.md)
  - [Worker area overview](worker/README.md)
