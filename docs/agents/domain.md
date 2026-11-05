# Domain Docs

How the engineering skills should consume this repo's domain documentation when exploring the codebase.

This is a single-context repo: one glossary at `docs/GLOSSARY.md` and one decisions directory at `planning/decisions/`.

## Before exploring, read these

- **`docs/GLOSSARY.md`** (not at the repo root).
- **`planning/decisions/`**: read architecture decision records (ADRs) that touch the area you're about to work in.

If any of these files don't exist, **proceed silently**.
Don't flag their absence; don't suggest creating them upfront.
The `/domain-modeling` skill (reached via `/grill-with-docs` and `/improve-codebase-architecture`) creates them lazily when terms or decisions actually get resolved.

## File structure

```
/
├── docs/
│   └── GLOSSARY.md
└── planning/
    └── decisions/
        ├── 001-parquet-outputs.md
        └── 002-duckdb-for-aggregation.md
```

## Glossary location

The glossary lives at `docs/GLOSSARY.md`, **not** the repo root.
When a skill says to read or write `GLOSSARY.md` at the repo root, use `docs/GLOSSARY.md` instead.

## ADR location and naming

ADRs live in `planning/decisions/`, **not** `docs/adr/`.
When a skill says to write or read an ADR in `docs/adr/`, use `planning/decisions/` instead.

Name each ADR `NNN-<slug>.md`: a three-digit, zero-padded sequence number followed by a short kebab-case slug (e.g. `001-parquet-outputs.md`).
Number new ADRs one higher than the highest existing number, and refer to them as `Decision-001`, `Decision-002`, etc.

## Use the glossary's vocabulary

When your output names a domain concept (in an issue title, a refactor proposal, a hypothesis, a test name), use the term as defined in `docs/GLOSSARY.md`.
Don't drift to synonyms the glossary explicitly avoids.

If the concept you need isn't in the glossary yet, that's a signal: either you're inventing language the project doesn't use (reconsider) or there's a real gap (note it for `/domain-modeling`).

## Flag ADR conflicts

If your output contradicts an existing ADR, surface it explicitly rather than silently overriding:

> _Contradicts Decision-007 (parquet outputs), but worth reopening because…_
