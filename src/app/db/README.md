# DB

## What this folder contains

This folder contains the persistence-layer foundations for the backend.

- [`base.py`](base.py) defines the shared SQLAlchemy declarative base, metadata naming convention, and UTC timestamp helper.
- [`config.py`](config.py) provides lazy database URL, engine, and session-factory wiring.
- [`models.py`](models.py) defines the persisted domain types, including `Job`, `JobResult`, and the `JobStatus` lifecycle enum.
- [`__init__.py`](__init__.py) re-exports the small public surface of the persistence layer for the rest of the application.

## Why it exists

The DB layer exists as a separate concern so persistence structure stays explicit and reusable across the API, the worker, and migration wiring.

Within that boundary, this folder owns ORM foundations, session/bootstrap helpers, and the persisted job/result model layer. It does not own application workflow decisions, request handling, or migration history itself.

## Key files to read first

- [`models.py`](models.py): start here for the persisted job lifecycle and result shapes.
- [`config.py`](config.py): read this next for database URL, engine, and session-factory wiring.
- [`base.py`](base.py): use this to understand the shared ORM base and metadata conventions.
- [`../../../alembic/README.md`](../../../alembic/README.md): continue here for the migration layer that evolves this schema over time.

## What should not live here

- Application or business workflow logic belongs in higher-level areas such as the API, worker, or service helpers, not in the persistence layer.
- Versioned migration history belongs to [`../../../alembic/README.md`](../../../alembic/README.md), not to `src/app/db/`.
- Quickstart, architecture baseline, and contribution workflow belong to the root docs rather than this localized README.

## How this area is tested / validated

This area is validated mostly through the backend paths that exercise real persisted state:

- `tests/test_jobs_api.py` and `tests/test_job_upload_api.py` cover persisted job creation and read-side behavior through the public API.
- `tests/test_worker_lifecycle.py`, `tests/test_worker_recovery.py`, and `tests/test_worker_cleanup.py` exercise the worker paths that update job and result state over time.
- `tests/test_critical_flows.py` covers broader end-to-end backend paths that depend on the persistence layer.

As a complementary baseline, [`../../../alembic/README.md`](../../../alembic/README.md) documents the migration-side expectation around `alembic upgrade head`.

## Related docs

- [Repository overview](../../../README.md)
- [App package map](../README.md)
- [Alembic migration note](../../../alembic/README.md)
- [Technical baseline and lifecycle](../../../ARCHITECTURE.md)
- [Contribution workflow and validation baseline](../../../CONTRIBUTING.md)
