# Implementation plan sketch for issue #51

This is a rough plan, not a finalized design.
It hasn't been through the same decision-by-decision grilling as `docs/analysis_groups_format.md` (issue #50) — treat it as a starting point for a follow-up design session once #50's format is settled, not as something to implement as-is.

## Parser rewrite (`parseGroupings` / `normalizeGroupings`)

`subworkflows/local/utils_nfcore_morphport_pipeline/main.nf`.

- Parse each entry as an object: `grouping_column` (required, single string), `groups` (required, either the literal string `one_per_value` or a `Map<String, List>`), `always_include` (optional list), `excluded` (optional list), `name` (optional string).
- Auto-generate `name` from `grouping_column` when omitted, and error on collisions between entries that resolve to the same name at the same level — including `default` entries, which get merged into both `cell` and `well`, so collision detection has to happen after that merge, not before it.
- Reject entries with more than one grouping column, and reject a missing `groups` key, at parse time — these don't need observed data to check.

## What can't be validated at parse time

The "every observed value must be accounted for" rule (`docs/analysis_groups_format.md`) needs the set of values actually observed in the platemaps, which isn't known until `enumeratePlatemapCombos` runs per plate and gets `.collect()`ed — well after `parseGroupings` runs on the YAML alone.
That check likely belongs in `buildAnalysisGroups()`, where `plate_combos` is already available, rather than in the parser.
This changes `buildAnalysisGroups()` from a pure size-computation function into one that can also fail the run — worth deciding deliberately rather than backing into it.

## `buildAnalysisGroups()`

- For `one_per_value` entries: unchanged from today (`plate_combos.countBy { subMap(...) }`).
- For named-group entries: compute, for each named group, the expected count by summing over every value that belongs to it (its own listed values, plus every `always_include` value) — a plate can count toward more than one group's expected size.
- Run the accounting-rule check here: diff the distinct observed values for the entry's `grouping_column` against (named group values ∪ `always_include` ∪ `excluded`); error if anything remains.
- Produce, per entry, a value → group-name(s) lookup that `createGroupChannels()` can use — a list, not a single value, since fan-out means one raw value can resolve to more than one group (explicit multi-group membership, or implicit via `always_include`).

## `createGroupChannels()`

`workflows/morphport.nf`.

- Today's `flatMap` produces exactly one tuple per (partition, matching grouping) pair. It needs to become one tuple per (partition, matching grouping, matching group) — i.e. a partition matching two named groups (explicit fan-out or `always_include`) emits two tuples, not one.
- Open question: what does `meta[grouping_column]` become for a named-group tuple — the group name (e.g. `Metadata_treatment: 'kinase_inhibitor'`) replacing the raw observed value? That's the natural choice (it's what makes the `groupKey` distinct per group, and what the existing `meta.grouping_columns?.collect { col -> "${col}=${meta[col]}" }` log pattern would print), but whether the original raw value also needs to travel somewhere for provenance (e.g. a `matched_value` field) hasn't been decided.
- A value that resolves to zero groups (an `excluded` value under named `groups`) should simply produce no tuples for that entry — not an error at this stage, since the accounting rule was already enforced upstream in `buildAnalysisGroups()`.

## Testing

`workflows/tests/createGroupChannels.nf.test` needs new cases: fan-out into multiple named groups, `always_include` duplicating a value across every group, `excluded` producing zero emissions, and (likely in a different test file, closer to `buildAnalysisGroups`) the accounting-rule error and the name-collision error.

## Fixture migration

Once the parser understands the new format, migrate `stub/analysis_groups.yml` and `assets/analysis_groups_example.yaml` to it, and update the `analysis_groups_file` help text in `nextflow_schema.json` to match `docs/analysis_groups_format.md`. This is the point where the format and the runtime flip over together.

## Open questions for the #51 design session

- Where `meta[grouping_column]` gets its value for named/fan-out groups, and whether the raw matched value needs separate provenance.
- Whether any other consumer of `group_meta` (output paths, publishDir patterns, portal-facing metadata) assumes a grouping column's value is a single raw observed value rather than a synthetic group name — needs an audit before this ships.
- Whether fan-out's duplicated parquet-file references have any meaningful cost downstream (expected to be negligible — references, not copies — but not yet confirmed).
