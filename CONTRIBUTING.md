# Contributing

This document defines the initial contribution workflow for `speech-jobs-backend`.

The repository is being built incrementally as a backend portfolio project. Contributions, including AI-assisted changes, must preserve clarity, stability, and traceability.

## Contribution Principles

- Keep changes small, focused, and reviewable.
- Prefer stable, verifiable progress over large speculative changes.
- Do not mix unrelated work in the same change.
- Do not rewrite or reorder working code without a strong reason.
- Respect the current scope of v1.
- Update project documents when the implemented reality changes.

## Local Environment

Contributors must work with:

- Python 3.12.6
- a local virtual environment at `/.venv`
- project dependencies installed inside `/.venv`
- version-pinned dependencies reflected in `requirements.txt`

Do not install project dependencies into the global Python environment.

## Setup Expectations

The exact setup commands may evolve, but the expected workflow is:

1. Clone the repository.
2. Create and activate `/.venv`.
3. Install the required dependencies.
4. Install the project in editable mode with `pip install -e .`.
5. Confirm that the selected interpreter belongs to `/.venv`.
6. Run the available validation commands before considering the work complete.

When schema changes are introduced, the expected local workflow now also includes Alembic commands such as:

- `alembic revision --autogenerate -m "your message"`
- `alembic upgrade head`
- `alembic current`

## Validation Expectations

Before a contribution is considered complete, validate at least the parts that were affected.

Expected validation categories will progressively include:

- formatting and linting checks
- unit tests
- integration checks for critical flows
- manual verification when automation is not yet available

Do not claim completion without evidence that the modified area still works.

## Documentation Drift

After each meaningful change, review whether the following documents need updates:

- `AGENTS.md`
- `PROJECT_CONTEXT.md`
- `ARCHITECTURE.md`
- `CODING_RULES.md`
- `MILESTONES.md`
- `EXECUTION_PLAN.md`
- `DELIVERY_LOG.md`

Only update the files that are actually affected. Avoid cosmetic rewrites.

## Commit Discipline

Commits should be:

- small and coherent
- in plain English
- easy to understand at a glance
- representative of the main change

Preferred style:

- short sentence
- no cryptic abbreviations
- no unnecessary jargon

Examples:

- `Add project planning documents`
- `Set up Python project files`
- `Add job database models`

Commits are created manually by the project owner, but suggested commit messages may be proposed during implementation work.

## Refactoring Rules

Refactoring is allowed only when it clearly improves the code and remains proportional to the task.

Avoid:

- opportunistic file-wide rewrites
- large formatting-only edits mixed with functional changes
- broad renaming without need
- speculative abstractions introduced too early

If a refactor is necessary, keep it explicit and narrowly scoped.

## Encoding and File Integrity

All text files must remain:

- UTF-8 encoded
- readable
- free of broken special characters

If a change introduces encoding corruption, it must be fixed in the same contribution.

## Delivery Tracking

Meaningful completed work should be reflected in `DELIVERY_LOG.md`.

Entries should be factual and brief. This file is not a diary.

## Quality Gates

Some steps in the project are followed by a **Quality Gate**.

A Quality Gate is a planned checkpoint used to review:

- technical consistency
- documentation consistency
- scope control
- implementation stability
- readiness for the next stage

When a contribution reaches a Quality Gate, do not proceed mechanically. Review the repository state before continuing.

## Scope Reminder

This repository is intentionally focused on a backend-first v1.

The following are out of scope for the first version unless the project definition is explicitly updated:

- frontend development
- authentication
- multi-user support
- cloud production deployment
- Celery or distributed task orchestration
- speaker identification by real identity
- advanced observability platforms

Contributions should reinforce the project goals, not expand them without justification.
