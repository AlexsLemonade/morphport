# Analysis groups file format (draft)

This document specifies the redesigned analysis groups file format defined for [issue #50](https://github.com/AlexsLemonade/morphport/issues/50).
It replaces the format described in the current `analysis_groups_file` help text in `nextflow_schema.json`.

> [!NOTE]
> This document defines the format only.
> The parser (`parseGroupings`/`normalizeGroupings`), `buildAnalysisGroups()`, and `createGroupChannels()` do not implement it yet — that is the scope of [issue #51](https://github.com/AlexsLemonade/morphport/issues/51).
> `nextflow_schema.json`, `assets/analysis_groups_example.yaml`, and `stub/analysis_groups.yml` still describe and use the old format until #51 lands.
> A standalone example of this new format lives at `docs/analysis_groups_example.yaml`, not wired into any pipeline config.

## Top-level structure

Unchanged from today: an optional `default` key, plus `cell` and/or `well` keys.
Each value is a list of grouping entries.
`default` entries are added to both `cell` and `well` (de-duplicated), exactly as before.

```yaml
default:
  - <entry>
cell:
  - <entry>
well:
  - <entry>
```

If the file is omitted entirely, both levels collapse to a single `one_per_value` grouping with no grouping column (i.e. one all-encompassing group per level) — the same fallback as today.

## Entry shape

Every entry is an object:

```yaml
- name: moa_grouping # optional; see "Entry name" below
  grouping_column: treatment # required; exactly one column
  groups: one_per_value # required; see "Groups" below
  always_include: [DMSO] # optional
  excluded: [vehicle_control_2] # optional
```

### `grouping_column` (required)

Exactly one metadata column, with or without the `Metadata_` prefix (the prefix is added automatically if missing, same as today).
Unlike the current format, a single entry can no longer name multiple columns — there is no comma-separated or list form.
Multi-column grouping is deferred: nothing in the motivating use cases (MoA/target mapping, shared controls) needs it, and it would require value-tuples instead of flat value lists throughout `groups`/`always_include`/`excluded`.

### `groups` (required)

No implicit default: every entry must state how its grouping column's values become groups.
Two forms:

**1. `groups: one_per_value`**

One analysis group per distinct value observed in the data for this column — the same behavior the current format always has.

**2. An explicit mapping of group name → list of values**

```yaml
groups:
  kinase_inhibitor: [drugA, drugB, drugC]
  hdac_inhibitor: [drugD]
```

Each key is a group name; each value is the list of raw column values that belong to that group.
A value may appear in more than one named group — the resulting samples belong to both groups (fan-out), not one or the other.
This is how a drug with two mechanisms of action ends up correctly analyzed under each one.

### `always_include` (optional)

A list of values whose data joins **every** group this entry produces, in addition to that value's own normal membership.
Works with both `groups: one_per_value` and named `groups`. Two examples:

- Named groups: `always_include: [DMSO]` alongside MoA groups means DMSO's samples appear in the `kinase_inhibitor` group, the `hdac_inhibitor` group, and any other named group in this entry.
- `one_per_value`: `always_include: [DMSO]` means every per-treatment group (one per distinct treatment value) also includes DMSO's samples, i.e. "every drug vs. DMSO."

### `excluded` (optional)

A list of values that deliberately produce or join **no** group under this entry.
Distinct from `always_include`'s counterpart concept in one direction only: `excluded` removes a value from consideration entirely for this entry, it does not mean "form its own group but exclude it from others."

### The accounting rule for named `groups`

When `groups` is an explicit name → value-set mapping, every value observed in the data for that grouping column must appear in at least one of: a named group, `always_include`, or `excluded`.
A value that appears in none of these is an **error**, not a warning — the pipeline should fail rather than silently produce an incomplete or surprising set of groups.
This is exactly what `excluded` is for: a deliberately-unmapped value (e.g. a rarely-used compound with no known MoA yet) is listed there to satisfy the accounting rule without being part of any group.

This rule does not apply to `groups: one_per_value` — every observed value automatically forms its own group, so there is no "unaccounted for" case.
`excluded` still works there, but purely as an opt-out (a value that shouldn't form its own group), never as error-avoidance.

### `name` (optional)

A label for the entry itself, used only for diagnostics (error messages, logs) — distinct from the group names inside `groups`, which name individual value-set groups.
If omitted, it's auto-generated from `grouping_column` (`Metadata_` prefix stripped) — e.g. an entry with `grouping_column: treatment` and no `name` auto-generates to `treatment`.

If two entries at the same level would both auto-generate the same name (e.g. two different `treatment`-based entries, neither with an explicit `name:`), that's a parse-time **error**: at least one needs an explicit `name:` to disambiguate.

## Partitioning is unaffected

`PARTITION_CONDITIONS` splits plate data by the union of every entry's `grouping_column`, across every level and `default` — unchanged from today's `partition_columns` computation.
Restricting each entry to a single grouping column does not restrict partitioning to a single column: the same fine-grained partitions are still shared and recombined across every entry, however many distinct grouping columns are in use across the file.

## Full example

```yaml
default:
  - grouping_column: treatment
    groups: one_per_value

cell:
  - name: moa_grouping
    grouping_column: treatment
    groups:
      kinase_inhibitor: [drugA, drugB, drugC]
      hdac_inhibitor: [drugD]
    always_include: [DMSO]
    excluded: [vehicle_control_2]

well:
  - grouping_column: cell_type
    groups: one_per_value
```

## What this format does not (yet) support

- **Multi-column combo grouping.** The current format's `treatment,cell_type` shorthand (one group per distinct value-combination across multiple columns) has no equivalent here.
  Deferred for now; revisit if a real use case needs it.
- **Multi-column value-sets.** A single named group defined by value-sets across more than one column (e.g. `treatment` in `[...]` AND `cell_type` in `[...]`) is out of scope for the same reason.
