# Examples

## What this folder contains

This folder contains the public demo audio assets currently bundled with the repository.

- [`audio/monologue_james_6m20s.m4a`](audio/monologue_james_6m20s.m4a)
- [`audio/conversation_two_speakers_10m.m4a`](audio/conversation_two_speakers_10m.m4a)

## Why it exists

This folder exists to give the repository a small, reproducible set of local example inputs for demos and onboarding.

It owns how those bundled assets are used inside the repo today. It does not own the broader architecture story, contribution workflow, or the full third-party inventory.

## Key files to read first

- [`audio/monologue_james_6m20s.m4a`](audio/monologue_james_6m20s.m4a): the canonical quickstart/demo audio referenced by the root `README.md`.
- [`audio/conversation_two_speakers_10m.m4a`](audio/conversation_two_speakers_10m.m4a): an additional local example resource associated with multi-speaker-oriented review and demos.

## What should not live here

- This folder should not absorb the full third-party or licensing inventory; that stays centralized in [`THIRD_PARTY.md`](../THIRD_PARTY.md).
- Architecture detail and contribution workflow belong to the root docs, not here.
- Temporary or generated test fixtures should not be treated as public example assets.
- The current repo does not include a dedicated silent demo asset under `examples/audio/`; silence-oriented cases in tests are generated as temporary fixtures instead.

## How this area is tested / validated

These bundled files are documented and validated mainly through their public usage in the repository:

- [`README.md`](../README.md) references `audio/monologue_james_6m20s.m4a` as the canonical quickstart/demo audio.
- [`THIRD_PARTY.md`](../THIRD_PARTY.md) keeps the short public reference for both bundled assets.
- The automated suite mostly uses synthetic or temporary audio fixtures rather than depending directly on these bundled `.m4a` files.

The current public repo supports the role of each file inside the project, but it does not yet support stronger claims about upstream provenance or licensing beyond the short notes already kept in `THIRD_PARTY.md`.

## Related docs

- [Repository overview](../README.md)
- [Third-party and asset notes](../THIRD_PARTY.md)
- [Technical baseline and lifecycle](../ARCHITECTURE.md)
