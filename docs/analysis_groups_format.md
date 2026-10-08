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
  filter: { timepoint: [24h] } # optional; see "Filter" below
  groups: one_per_value # required; see "Groups" below
  always_include: [DMSO] # optional
  excluded: [vehicle_control_2] # optional
```

### `grouping_column` (required)

Exactly one metadata column, with or without the `Metadata_` prefix (the prefix is added automatically if missing, same as today).
Unlike the current format, a single entry can no longer name multiple columns — there is no comma-separated or list form.
Multi-column grouping is deferred: nothing in the motivating use cases (MoA/target mapping, shared controls) needs it, and it would require value-tuples instead of flat value lists throughout `groups`/`always_include`/`excluded`.

### `filter` (optional)

A restriction on which rows this entry sees at all, applied **before** any grouping happens.
Where `always_include` and `excluded` act on the values of `grouping_column`, `filter` acts on _other_ metadata columns — it is what lets an entry say "group by cell line, but only in untreated wells."

```yaml
filter:
  treatment: [DMSO, untreated]
```

Each key is a metadata column (with or without the `Metadata_` prefix, same normalization as `grouping_column`); each value is the list of column values to **keep**.
Multiple columns are combined with AND: a row must match every listed column to be included.
Listing a single value still requires list form (`treatment: [DMSO]`), so the shape is uniform.

`filter` may name the entry's own `grouping_column`, though `excluded` is usually the clearer way to express that.

#### How `filter` interacts with everything else

- **It applies first, and to all rows.** `always_include` values are subject to the filter too — an entry that filters `treatment` down to `[DMSO]` and then says `always_include: [drugA]` contributes no drugA rows, because they were already filtered out. That combination is a mistake and should be an **error** rather than a silent empty contribution. Note that it is only detectable without reading the data when `filter` names the entry's own `grouping_column`, since `always_include` lists values of that column; a filter on some _other_ column (`filter: {timepoint: [24h]}` with `always_include: [DMSO]`) fails only if DMSO never appears at 24h, which takes observed values to know. See "How errors are raised" below.
- **"Observed values" means observed after filtering.** Both the per-value group enumeration of `one_per_value` and the accounting rule for named `groups` consider only values present in the filtered rows. A treatment value that exists in the data but is filtered out of this entry does not need to be mentioned in `excluded` to satisfy the accounting rule.
- **An entry whose filter matches no rows is an error.** A filter naming a value that never appears in the data is far more likely to be a typo than an intentional empty group.
- **A filter column missing from any platemap is an error.** This is specific to `filter` and has no analogue in the current format. `enumeratePlatemapCombos` encodes a column absent from a given platemap as the sentinel `_Absent_` (`subworkflows/local/utils_nfcore_morphport_pipeline/main.nf:462`), which matches no ordinary filter value — so a plate whose platemap happens to omit `treatment` would contribute **zero rows** to a `filter: {treatment: [...]}` entry, silently, while other plates matched normally. Grouping columns degrade gracefully here (the rows form an `_Absent_` group and are still visible); filters just drop the data. Every filter column must therefore be present in _every_ plate's platemap.
- **Auto-generated names collide more easily.** Two entries that differ _only_ by `filter` share a `grouping_column` and therefore auto-generate the same name, which is already an error (see "Entry name"). Filtered entries will usually want an explicit `name:`.

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
Because it rests on observed values, this check cannot run until the platemaps have been read (see "How errors are raised").
This is exactly what `excluded` is for: a deliberately-unmapped value (e.g. a rarely-used compound with no known MoA yet) is listed there to satisfy the accounting rule without being part of any group.

This rule does not apply to `groups: one_per_value` — every observed value automatically forms its own group, so there is no "unaccounted for" case.
`excluded` still works there, but purely as an opt-out (a value that shouldn't form its own group), never as error-avoidance.

### `name` (optional)

A label for the entry itself, used only for diagnostics (error messages, logs) — distinct from the group names inside `groups`, which name individual value-set groups.
If omitted, it's auto-generated from `grouping_column` (`Metadata_` prefix stripped) — e.g. an entry with `grouping_column: treatment` and no `name` auto-generates to `treatment`.

If two entries at the same level would both auto-generate the same name (e.g. two different `treatment`-based entries, neither with an explicit `name:`), that's an **error** detectable from the file alone: at least one needs an explicit `name:` to disambiguate.

## Partitioning

`PARTITION_CONDITIONS` splits plate data by the union of every entry's `grouping_column` **and every column named in any entry's `filter`**, across every level and `default`.
This is the one place `filter` changes existing behavior: today's `partition_columns` computation (`subworkflows/local/utils_nfcore_morphport_pipeline/main.nf:112`) is a union of grouping columns only, and it has to grow to include filter columns.

The reason is mechanical: groups are assembled from whole partitions, so an entry can only filter on a column if the partitions are fine-grained enough to separate that column's values.
Grouping by `cell_type` while filtering on `treatment` requires partitions split by both, even though no entry groups by the two together.

Restricting each entry to a single grouping column does not restrict partitioning to a single column: the same fine-grained partitions are still shared and recombined across every entry, however many distinct columns are in use across the file.

Note the cost: each distinct filter column multiplies the partition count, and partitions are the unit of work for everything downstream of `PARTITION_CONDITIONS`.
A filter column with many distinct values is more expensive than one with two or three.

## How errors are raised

The checks above split into two tiers by what they need to know, and the tiers fail at different points in the run.
All file/line references are to `subworkflows/local/utils_nfcore_morphport_pipeline/main.nf`.

### Tier 1: structural — knowable from the file alone

Raised from `parseGroupings` (called at line 111), which runs in the workflow body rather than inside a channel operator, so `error()` there aborts before any process is submitted.
This is the same idiom as the existing cross-parameter checks at lines 93-101.

- malformed shapes (`filter` not a map, values not lists, `groups` neither `one_per_value` nor a mapping)
- missing required keys
- duplicate auto-generated entry names
- `always_include` conflicting with `filter` **when `filter` names the entry's own `grouping_column`** — the only form of that conflict detectable without data

### Tier 2: data-dependent — needs observed values

The accounting rule, the zero-match filter check, the missing-filter-column check, and the cross-column form of the `always_include` conflict all need the set of values actually present in the platemaps.
That set comes from `enumeratePlatemapCombos` (line 444) and is only complete at line 153, in the `.map{}` that calls `buildAnalysisGroups`.

Raising from inside a channel operator works and is already the established pattern — `validateInputSamplesheet` is invoked inside a `.map{}` at line 118 and calls `error()` at line 286.

**But line 153 is too late to fail.**
`ch_samplesheet` feeds `ch_validated` (`workflows/morphport.nf:33`) and the CytoTable processes directly, so by the time the `buildAnalysisGroups` closure runs those tasks have already been submitted.
On AWS Batch that means a typo in the groups file costs real compute before it surfaces.

The recommended implementation is therefore to hoist the value-dependent checks into the `validateInputSamplesheet` closure at line 118.
That closure runs on `.toList()`, already has every resolved `platemap_file`, and sits upstream of the first `ch_samplesheet` emission — nothing has launched yet when it raises.
It needs the platemap combos, currently enumerated later at line 149, so the implementation has to either thread that result forward or accept enumerating twice.

### Message style

Errors should name the offending entry by its `name` (explicit or auto-generated), since that is what `name` exists for.
Prefer collecting all problems and reporting them together, as `validateInputSamplesheet` does at lines 285-289, rather than failing on the first one — a groups file with three typos should report three.

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

  # One group per cell line, restricted to wells with no drug treatment --
  # a baseline cell-line comparison uncontaminated by treatment effects.
  - name: untreated_cell_lines
    grouping_column: cell_type
    filter:
      treatment: [DMSO, untreated]
    groups: one_per_value

well:
  - grouping_column: cell_type
    groups: one_per_value
```

## What this format does not (yet) support

- **Multi-column combo grouping.** The current format's `treatment,cell_type` shorthand (one group per distinct value-combination across multiple columns) has no equivalent here.
  A filtered entry per value gets part of the way there by hand (one `grouping_column: treatment` entry per cell line, each with `filter: {cell_type: [...]}`), but it is static: the file has to be edited whenever a new cell line enters the data, where combo grouping would pick it up automatically.
  Still deferred; revisit if hand-enumeration becomes the common case.
- **Multi-column value-sets.** A single named group defined by value-sets across more than one column (e.g. `treatment` in `[...]` AND `cell_type` in `[...]`) is out of scope for the same reason.
- **Negative filters.** `filter` is keep-only; there is no "every value except these" form.
  For the grouping column itself `excluded` covers it, and the motivating filters (control wells, a timepoint) are short enumerable lists.
  A column with many values where the exclusion list is shorter than the inclusion list would make the case for adding one.
