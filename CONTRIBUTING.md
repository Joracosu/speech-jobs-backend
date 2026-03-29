# Before you change anything

Keep changes small, technically grounded, and easy to review.

- Verify the behavior you are changing.
- Avoid mixing unrelated edits in one change set.
- Update only the public docs that are actually affected.
- Prefer the implemented repository state over stale wording or assumptions.

## Minimal validation baseline

For most repository changes, the minimum public validation baseline is:

```bash
alembic upgrade head
pytest -q
python -c "from app.main import app; print('ok app', app.title, app.version)"
```

Add more targeted checks when your change affects a narrower or riskier area, but keep the baseline small and sustainable.

## Accepted change types

- `bug fix`: correct a real defect without expanding scope.
- `docs`: improve public documentation so it matches the implemented repository.
- `tests`: tighten or extend coverage around existing behavior.
- `small isolated feature`: add one bounded capability that fits the current backend baseline.
- `migrations`: evolve persisted schema intentionally and keep Alembic history aligned.

## Change type -> expected updates

| change type | expected updates | minimum validation |
| --- | --- | --- |
| `bug fix` | Update affected tests and public docs if user-visible behavior changed. Update `ARCHITECTURE.md` if the technical baseline, lifecycle, public contract, or explicit boundaries changed. | Public baseline plus targeted evidence for the fixed area |
| `docs` | Update only the docs touched by the real behavior change. Keep public docs free of internal jargon, delivery tracking, and private references. | Verify commands, paths, filenames, and claims against the repo |
| `tests` | Keep tests aligned with real behavior. Update docs only if the tests reveal stale public wording. | `pytest -q` plus any targeted checks needed by the changed tests |
| `small isolated feature` | Add focused tests, update public docs, and update `ARCHITECTURE.md` when the baseline, lifecycle, contract, or boundaries move. | Full public baseline plus targeted validation for the new behavior |
| `migrations` | Keep Alembic state, persistence behavior, and affected docs aligned. Add or adjust tests when schema-backed behavior changes. | `alembic upgrade head`, `pytest -q`, and the app import check |

## Documentation touchpoints

- Update `README.md` when the quickstart, public-facing API examples, runtime entry points, or repo navigation change.
- Update `ARCHITECTURE.md` when the technical baseline, lifecycle, public contract, or explicit system boundaries change.
- Update `CONTRIBUTING.md` when the safe-change workflow or the minimal validation baseline changes.
- More localized READMEs are planned later. Do not reference them as if they already exist.

## Source of truth

Observed behavior wins over stale prose. Code defines what is currently implemented, and public documentation must match that implemented baseline instead of describing an intended future state.

## Keep changes focused

- Do not mix unrelated fixes, refactors, docs cleanup, and feature work in one change set.
- Do not use public docs to expose runs, delivery logs, prompts, AI workflow notes, or private-document references as normal reading.
- If a change does not belong to the current scope, leave it for a separate follow-up.

## Final checklist

- The change is small, coherent, and reviewable.
- The affected behavior was validated with the minimum baseline and any necessary targeted checks.
- Tests, migrations, and docs were updated only where the change truly requires them.
- `README.md`, `ARCHITECTURE.md`, and `CONTRIBUTING.md` still respect their ownership boundaries.
- Public-facing text stays free of internal jargon, delivery tracking, and private workflow references.
