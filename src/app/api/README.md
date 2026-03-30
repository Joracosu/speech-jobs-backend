# API

## What this folder contains

This folder contains the public HTTP layer of the backend.

- [`routes/`](routes/) exposes the request handlers for health and job-related endpoints.
- [`schemas/`](schemas/) defines the public response models returned by that layer.
- [`dependencies.py`](dependencies.py) provides shared request-scoped dependencies such as the database session.

## Why it exists

The API layer keeps the HTTP boundary explicit and thin. It accepts requests, validates public inputs, projects persisted state to the public contract, and hands off longer-running work to the rest of the backend.

That separation keeps worker execution, storage internals, and persistence ownership out of the request path while still giving the repository one clear place for request-layer behavior.

## Key files to read first

- [`routes/jobs.py`](routes/jobs.py): start here for the main job upload, list, detail, and result handlers.
- [`routes/health.py`](routes/health.py): read this for the minimal liveness surface.
- [`schemas/jobs.py`](schemas/jobs.py): use this to understand the public jobs/result response shape.
- [`dependencies.py`](dependencies.py): read this for the request-scoped session dependency.

## What should not live here

- Worker processing, recovery, and cleanup logic belong to the worker package, not the API layer.
- Deep local storage or ffprobe handling belongs to narrower backend helpers such as `services/uploads.py`.
- SQLAlchemy model ownership belongs to the persistence layer under `db/`.
- This README should not become an endpoint catalog or a replacement for the full architecture document.

## How this area is tested / validated

Relevant checks here stay focused on the public HTTP surface:

- `tests/test_health.py` covers the liveness endpoint.
- `tests/test_job_upload_api.py` and `tests/test_jobs_api.py` cover the upload and read-side API contract.
- `tests/test_critical_flows.py` exercises the broader backend path in which the API participates.

The full suite taxonomy belongs to [`tests/README.md`](../../../tests/README.md), not to this localized API document.

## Related docs

- [Repository overview](../../../README.md)
- [App package map](../README.md)
- [Technical baseline and lifecycle](../../../ARCHITECTURE.md)
- [Contribution workflow and validation baseline](../../../CONTRIBUTING.md)
