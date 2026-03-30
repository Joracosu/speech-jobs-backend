# Tests

## What this folder contains

This folder contains the automated test suite for the public backend slice.

- API contract tests cover health, upload acceptance, and read-side result behavior.
- Worker tests cover lifecycle, recovery, cleanup, runtime checks, silence handling, and adapter boundaries.
- A small critical-flow layer checks high-value backend paths across API, worker, and persistence.

## Why it exists

This folder exists to keep the implemented backend baseline verifiable and reviewable.

It owns the test taxonomy for the repository and gives contributors a small map for extending the suite without turning `tests/` into a filename dump or a mirror of the source tree.

## Key files to read first

- [`test_critical_flows.py`](test_critical_flows.py): start here for the highest-value cross-boundary backend flows.
- [`test_jobs_api.py`](test_jobs_api.py) and [`test_job_upload_api.py`](test_job_upload_api.py): read these for the public upload and read-side contract layer.
- [`test_worker_lifecycle.py`](test_worker_lifecycle.py): use this as the main entry point for worker processing behavior.
- [`test_worker_recovery.py`](test_worker_recovery.py) and [`test_worker_cleanup.py`](test_worker_cleanup.py): use these for the worker's bounded operational duties.

## What should not live here

- This folder should not become a second architecture document or a second quickstart.
- Public API explanation belongs to the root docs; tests here should validate that contract, not replace its documentation.
- New tests should join the closest intent bucket that already exists instead of creating one-off files by convenience.
- This folder should not turn into a mirror of the source tree where every module automatically gets its own test file without a real coverage reason.

## How this area is tested / validated

The suite is organized primarily by test intent:

- Smoke and baseline health: `test_health.py`.
- Upload and read-side API contract: `test_job_upload_api.py` and `test_jobs_api.py`.
- Worker processing lifecycle: `test_worker_lifecycle.py`.
- Worker operational behavior: `test_worker_main.py`, `test_worker_recovery.py`, and `test_worker_cleanup.py`.
- Worker adapters and narrow helpers: `test_worker_asr.py`, `test_worker_diarization.py`, `test_worker_runtime_checks.py`, and `test_worker_silence.py`.
- Cross-boundary critical backend flows: `test_critical_flows.py`.

When adding coverage, prefer the category that already matches the intent of the change. Keep `test_critical_flows.py` for a small number of high-value paths that genuinely cross boundaries, and avoid scattering closely related behavior across too many files.

## Related docs

- [Repository overview](../README.md)
- [Technical baseline and lifecycle](../ARCHITECTURE.md)
- [Contribution workflow and validation baseline](../CONTRIBUTING.md)
- [Worker-specific runtime depth](../src/app/worker/README.md)
