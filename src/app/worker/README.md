# Worker

## What this folder contains

This folder contains the dedicated worker process and the worker-owned helpers that run audio jobs outside the HTTP request path.

- [`main.py`](main.py) exposes the local worker CLI, including `--once` and `--preflight`.
- [`service.py`](service.py) owns the main claim, processing, persistence, heartbeat, and reconciliation flow.
- [`runtime_checks.py`](runtime_checks.py) holds shared runtime readiness checks for ASR and diarization.
- [`silence.py`](silence.py) provides the deterministic silence inspection used before normal transcription.
- [`cleanup.py`](cleanup.py) provides TTL-based cleanup helpers for local inputs and optional local artifacts.
- [`asr.py`](asr.py) and [`diarization.py`](diarization.py) keep model-facing execution behind narrower worker-side adapters.

## Why it exists

The worker exists as a separate process so the API can stay focused on accepting uploads and exposing read-side state while heavy audio processing happens asynchronously.

Within that boundary, the worker owns the parts of the lifecycle that need durable claim state, longer-running execution, and bounded operational duties:

- `preflight`: check ASR and diarization runtime readiness without claiming or processing jobs.
- `silence short-circuit`: detect confident real silence early and complete the job with a coherent empty result instead of pushing silent audio through the normal path.
- `degraded diarization`: preserve useful ASR output when diarization fails in a controlled way instead of turning every downstream diarization issue into a full job failure.
- `stale-running reconciliation`: detect interrupted `running` jobs and reconcile them back to trustworthy terminal state.
- `heartbeat/recovery`: keep active jobs fresh while they are really being processed and use that liveness signal during recovery decisions.
- `cleanup`: remove expired local inputs and optional artifacts by bounded TTL rules without redefining persisted job history.

## Key files to read first

- [`main.py`](main.py): start here for the worker entrypoint, `--preflight`, and the startup recovery/cleanup hooks.
- [`service.py`](service.py): read this next for the main processing path, job claiming, heartbeat refresh, and stale-running reconciliation.
- [`runtime_checks.py`](runtime_checks.py): use this to understand what the worker considers "ready" before processing starts.
- [`silence.py`](silence.py): read this for the silence gate that can bypass the normal ASR and diarization path.
- [`cleanup.py`](cleanup.py): read this for local TTL cleanup behavior and its safety boundaries.

## What should not live here

- The public HTTP contract belongs to the API layer and the root docs, not to the worker folder.
- General repository navigation and quickstart belong to [`README.md`](../../../README.md).
- Contribution workflow and repository-wide validation guidance belong to [`CONTRIBUTING.md`](../../../CONTRIBUTING.md).
- The full system lifecycle and architectural baseline belong to [`ARCHITECTURE.md`](../../../ARCHITECTURE.md).
- Unrelated logic should not be placed in the worker package just because it is convenient to run in a background process.

## How this area is tested / validated

Relevant checks here are grouped by purpose rather than mapped exhaustively:

- CLI and preflight behavior: `tests/test_worker_main.py`.
- Core processing flow, including silence short-circuit and degraded diarization: `tests/test_worker_lifecycle.py`.
- Heartbeat handling and stale-running reconciliation: `tests/test_worker_recovery.py`.
- Local TTL cleanup behavior: `tests/test_worker_cleanup.py`.
- Narrower helpers and broader integrated flow coverage: `tests/test_worker_runtime_checks.py`, `tests/test_worker_silence.py`, and `tests/test_critical_flows.py`.

This section is intentionally narrower than a full test taxonomy. A future `tests/README.md` can own the complete suite map.

## Related docs

- [Repository overview](../../../README.md)
- [Technical baseline and lifecycle](../../../ARCHITECTURE.md)
- [Contribution workflow and validation baseline](../../../CONTRIBUTING.md)
- A future `tests/README.md` can expand the suite taxonomy once that localized README exists.
