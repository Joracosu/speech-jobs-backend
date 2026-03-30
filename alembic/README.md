# Alembic

## What this folder contains

This folder contains the tracked database migration wiring for the repository.

- [`env.py`](env.py) connects Alembic to the configured database URL and SQLAlchemy metadata.
- [`versions/`](versions/) holds the revision history, including the current baseline revisions.

## Why it exists

`alembic/` owns the migration history that keeps the persisted schema aligned with the backend models across local development, CI, and the documented project baseline.

This README stays intentionally operational: it tells readers where migration behavior lives and what the minimum migration expectation is.

## Key files to read first

- [`env.py`](env.py): start here for metadata wiring and database URL setup.
- [`versions/`](versions/): read the tracked revisions here, starting with [`be857cdb2cd2_initial_schema.py`](versions/be857cdb2cd2_initial_schema.py) and [`2dbbb7d95cf8_add_job_heartbeat_tracking.py`](versions/2dbbb7d95cf8_add_job_heartbeat_tracking.py).

## What should not live here

- Domain-model explanations belong to the application persistence layer, not to the migration folder.
- The full technical baseline belongs to [`ARCHITECTURE.md`](../ARCHITECTURE.md).
- The broader contributor workflow belongs to [`CONTRIBUTING.md`](../CONTRIBUTING.md).
- This folder should not become a long migration guide or a place for untracked schema notes.

## How this area is tested / validated

- `alembic upgrade head` is the minimum migration check for this repository.
- In the normal project baseline, the migration flow is expected to stay safe and repeatable when applied against the tracked revision state.
- That expectation is already aligned with the CI-backed migration discipline and the public validation baseline.

## Related docs

- [Repository overview](../README.md)
- [DB layer overview](../src/app/db/README.md)
- [Technical baseline and lifecycle](../ARCHITECTURE.md)
- [Contribution workflow and validation baseline](../CONTRIBUTING.md)
