# Services

## What this folder contains

This folder contains narrow service helpers for application workflows that do not belong directly in the HTTP layer, the worker runtime, or the persistence layer.

- [`uploads.py`](uploads.py) owns upload validation, ffprobe-based media inspection, local input storage, and the small helper types/errors used by that flow.
- [`__init__.py`](__init__.py) marks the package and keeps the service layer intentionally small.

## Why it exists

The services layer exists to hold reusable application helpers when a concern is part of the backend workflow but should not be embedded directly in route handlers, worker execution, or ORM wiring.

In the current repository state, that role is intentionally narrow and centers on the upload path. This folder should stay small and explicit rather than grow into a grab bag of unrelated logic.

## Key files to read first

- [`uploads.py`](uploads.py): start here for the current service-layer behavior in this repository.
- [`../api/routes/jobs.py`](../api/routes/jobs.py): read this next to see how the upload helper is called from the HTTP layer.
- [`../core/settings.py`](../core/settings.py): use this for the configuration values consumed by the upload workflow.

## What should not live here

- HTTP contract handling and request/response projection belong to the API layer, not to `services/`.
- Asynchronous processing, recovery, and cleanup belong to the worker package.
- ORM models, sessions, and database bootstrap helpers belong to the DB layer.
- This folder should not become a broad domain layer by convenience when a concern already has a clearer owner elsewhere.

## How this area is tested / validated

There is no standalone `tests/test_services_*.py` layer today. The current service helper is validated through the backend surfaces that exercise it:

- `tests/test_job_upload_api.py` covers the upload path that calls `store_uploaded_audio`.
- `tests/test_critical_flows.py` provides broader integrated coverage where the upload path participates in the end-to-end backend flow.

## Related docs

- [Repository overview](../../../README.md)
- [Technical baseline and lifecycle](../../../ARCHITECTURE.md)
- [Contribution workflow and validation baseline](../../../CONTRIBUTING.md)
- [App package map](../README.md)
- [API layer overview](../api/README.md)
