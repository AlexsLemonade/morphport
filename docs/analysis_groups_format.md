# Analysis groups file format (draft)

This document specifies the redesigned analysis groups file format defined for [issue #50](https://github.com/AlexsLemonade/morphport/issues/50).
It replaces the format described in the current `analysis_groups_file` help text in `nextflow_schema.json`.

> [!NOTE]
> This document defines the format only.
> The parser (`parseGroupings`/`normalizeGroupings`), `buildAnalysisGroups()`, and `createGroupChannels()` do not implement it yet — that is the scope of [issue #51](https://github.com/AlexsLemonade/morphport/issues/51).
> `nextflow_schema.json`, `assets/analysis_groups_example.yaml`, and `stub/analysis_groups.yml` still describe and use the old format until #51 is implemented.
> A standalone example of this new format lives at `docs/analysis_groups_example.yaml`, not wired into any pipeline config.

## Top-level structure

Unchanged from the previous version: an optional `default` key, plus `cell` and/or `well` keys.
Each value is a list of grouping criteria.
`default` entries are added to both `cell` and `well` (de-duplicated), exactly as before.

```yaml
default:
  - <entry>
cell:
  - <entry>
well:
  - <entry>
```

If no analysis groups file is given, no grouping or partitioning is performed: each level (`cell` and `well`) gets a single group containing all of a project's data.
This is the same behavior as currently implemented.

## Entry Format

Every entry is an object:

```yaml
- name: moa_grouping # optional; see "`name`" below
  grouping_column: treatment # required; exactly one column
  filter: { timepoint: [24h] } # optional; see "Filter" below
  groups: one_per_value # required; see "Groups" below
  always_include: [DMSO] # optional
  excluded: [vehicle_control_2] # optional
  subgroup_columns: [cell_type] # optional; see "subgroup_columns" below
```

### `grouping_column` (required)

Exactly one metadata column, with or without the `Metadata_` prefix (the prefix is added automatically if missing, same as today).
Unlike the current format, a single entry can no longer name multiple columns — there is no comma-separated or list form.
To repeat a grouping within each value of other columns, use `subgroup_columns`.

### `filter` (optional)

Allows selecting rows to include in the grouping sets, applied **before** any grouping happens.
Where `always_include` and `excluded` act on the values of `grouping_column`, `filter` acts on _other_ metadata columns — it is what lets an entry say "group by cell line, but only in untreated wells."

```yaml
filter:
  treatment: [DMSO, untreated]
```

Each key is a metadata column (with or without the `Metadata_` prefix, same normalization as `grouping_column`); each value is the list of column values to **keep**.
Multiple columns are combined with AND: a row must match every listed column to be included.
Listing a single value still requires list form (`treatment: [DMSO]`), so the shape is uniform.

`filter` must not name the entry's own `grouping_column`; use `excluded` to drop values of that column.

#### How `filter` interacts with everything else

- **It applies first, and to all rows.** `always_include` values are subject to the filter too — an entry with `filter: {timepoint: [24h]}` and `always_include: [DMSO]` contributes no DMSO rows if DMSO never appears at 24h. That is a mistake and should be an **error** rather than a silent empty contribution. Detecting it takes observed values; see "How errors are raised" below.
- **"Observed values" means observed after filtering.** Both the per-value group enumeration of `one_per_value` and the accounting rule for named `groups` consider only values present in the filtered rows. A treatment value that exists in the data but is filtered out of this entry does not need to be mentioned in `excluded` to satisfy the accounting rule.
- **An entry whose filter matches no rows is an error.** A filter naming a value that never appears in the data is far more likely to be a typo than an intentional empty group.
- **A filter column missing from any platemap is an error.** This is specific to `filter` and has no analogue in the current format. `enumeratePlatemapCombos` encodes a column absent from a given platemap as the sentinel `_Absent_`, which matches no ordinary filter value — so a plate whose platemap happens to omit `treatment` would contribute **zero rows** to a `filter: {treatment: [...]}` entry, silently, while other plates matched normally. Grouping columns degrade gracefully here (the rows form an `_Absent_` group and are still visible); filters just drop the data. Every filter column must therefore be present in _every_ plate's platemap.
- **Auto-generated names collide more easily.** Two entries that differ _only_ by `filter` (or `subgroup_columns`) share a `grouping_column` and therefore auto-generate the same name, which is already an error (see "`name`"). Filtered entries will usually want an explicit `name:`.

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
- `one_per_value`: `always_include: [DMSO]` means every per-treatment group (one per distinct treatment value) also includes DMSO's samples, i.e., "every drug vs. DMSO."

### `excluded` (optional)

A list of values that deliberately produce or join **no** group under this entry.
Distinct from `always_include`'s counterpart concept in one direction only: `excluded` removes a value from consideration entirely for this entry, it does not mean "form its own group but exclude it from others."
This is primarily designed to capture values that are excluded from groupings, such as drugs for which no MoA is defined, so that we can catch errors quickly when there are values that are not listed in the grouping sets but _should_ have been.

### The accounting rule for named `groups`

When `groups` is an explicit name → value-set mapping, every value observed in the data for that grouping column **must** appear in at least one of: a named group, `always_include`, or `excluded`.
A value that appears in none of these is an **error**, not a warning — the pipeline should fail rather than silently produce an incomplete or surprising set of groups.
Because it rests on observed values, this check cannot run until the platemaps have been read (see "How errors are raised").
This is exactly what `excluded` is for: a deliberately-unmapped value (e.g. a rarely-used compound with no known MoA yet) is listed there to satisfy the accounting rule without being part of any group.

This rule does not apply to `groups: one_per_value` — every observed value automatically forms its own group, so there is no "unaccounted for" case.
`excluded` still works there, but purely as an opt-out (a value that shouldn't form its own group), never as error-avoidance.

### `name` (optional)

A label for the entry itself, used only for diagnostics (error messages, logs) — distinct from the group names inside `groups`, which name individual value-set groups.
If omitted, it's auto-generated from `grouping_column` (`Metadata_` prefix stripped) — e.g. an entry with `grouping_column: treatment` and no `name` auto-generates to `treatment`.

If two entries at the same level would both auto-generate the same name (e.g., two different `treatment`-based entries, neither with an explicit `name:`), that's an **error** detectable from the file alone: at least one needs an explicit `name:` to disambiguate.

### `subgroup_columns` (optional)

A list of other metadata columns to subdivide the entry's groups by.
The entry's grouping is applied separately within each observed value (or value-combination) of the `subgroup_columns`, and every resulting group is tagged with those values.

```yaml
- name: moa_by_cell_line
  grouping_column: treatment
  groups:
    kinase_inhibitor: [drugA, drugB]
    hdac_inhibitor: [drugD]
  always_include: [DMSO]
  excluded: [drugC]
  subgroup_columns: [cell_type]
```

With two cell lines in the data, this produces four groups: `kinase_inhibitor` × `lineA`, `kinase_inhibitor` × `lineB`, `hdac_inhibitor` × `lineA`, and `hdac_inhibitor` × `lineB`.
Each contains only its own cell line's drug and DMSO wells.

Column names follow the same `Metadata_` normalization as `grouping_column`.
The value is always a list, even for a single column, matching `filter`.
`subgroup_columns` must not name the entry's own `grouping_column`.

**Subgroup columns cannot have defined groups.**
Each `subgroup_columns` column always produces one subgroup per observed value, the equivalent of `groups: one_per_value`.
There is no way to map subgroup column values into named groups, and `always_include` and `excluded` cannot refer to them; those keys apply only to `grouping_column`.
To restrict which subgroups exist, use `filter` (see above).

With `groups: one_per_value`, `grouping_column: treatment` with `subgroup_columns: [cell_type]` yields one group per observed (treatment, cell_type) pair, the same as the old format's `treatment,cell_type` shorthand.

#### How `subgroup_columns` interacts with everything else

- **It applies after `filter`.** Subgroups are enumerated from the filtered rows, so `filter` can restrict which subgroups exist: `filter: {cell_type: [lineA, lineB]}` with `subgroup_columns: [cell_type]` means "subdivide by cell line, but only for these two lines." Naming the same column in both is allowed.
- **`always_include` is applied within each subgroup.** In the example above, each cell line's groups get DMSO wells (from that cell line), so every drug is compared against a matched control.
- **An `always_include` value missing from a subgroup is an error.** If a cell line has no DMSO wells, its groups would silently lack the shared control the entry asked for. This is checked per subgroup, against observed values (Tier 2).
- **`excluded` and the accounting rule apply across all subgroups combined.** They are statements about values of `grouping_column`, regardless of subgroup: a value is accounted for if it is named anywhere in the entry, and need not appear in every subgroup. A named group that happens to have no members in a particular subgroup simply produces no group for that subgroup.
- **Subgroup columns behave like grouping columns when absent from a platemap.** Rows from a plate whose platemap omits a `subgroup_columns` column form an `_Absent_` subgroup, the same graceful degradation as `grouping_column`, rather than being dropped as `filter` would.

## Partitioning updates

`PARTITION_CONDITIONS` will need to split plate data by the union of every entry's `grouping_column`, `subgroup_columns`, **and every column named in any entry's `filter`**, across every level and `default`.
This is the one place `filter` changes existing behavior: today's `partition_columns` computation in `PIPELINE_INITIALISATION` is a union of grouping columns only, and it has to grow to include filter columns.

Restricting each entry to a single grouping column does not restrict partitioning to a single column: the same fine-grained partitions are still shared and recombined across every entry, however many distinct columns are in use across the file.

Note the cost: each distinct filter column multiplies the partition count, and partitions are the unit of work for everything downstream of `PARTITION_CONDITIONS`.
A filter column with many distinct values is more expensive than one with two or three.

## How errors are raised

The checks above split into two tiers by what they need to know, and the tiers fail at different points in the run.
Functions and variables named here are in `subworkflows/local/utils_nfcore_morphport_pipeline/main.nf` unless noted, mostly in the `PIPELINE_INITIALISATION` workflow.

### Tier 1: structural — knowable from the file alone

Raised from `parseGroupings`, which runs in the workflow body rather than inside a channel operator, so `error()` there aborts before any process is submitted.
This is the same idiom as the existing cross-parameter checks (e.g. `feature_select_operations`, `clustering_linkage_method`) just above it.

- malformed shapes (`filter` not a map, values not lists, `subgroup_columns` not a list, `groups` neither `one_per_value` nor a mapping)
- missing required keys
- duplicate auto-generated entry names
- `filter` naming the entry's own `grouping_column`
- `subgroup_columns` naming the entry's own `grouping_column`

### Tier 2: data-dependent — needs observed values

The accounting rule, the zero-match filter check, the missing-filter-column check, the per-subgroup `always_include` check, and the check for `always_include` values removed by `filter` all need the set of values actually present in the platemaps.
That set comes from `enumeratePlatemapCombos` and is only complete in the `.map{}` that calls `buildAnalysisGroups` while building `ch_analysis_groups`.

Raising from inside a channel operator works and is already the established pattern — `validateInputSamplesheet` is invoked inside a `.map{}` while building `ch_samplesheet` and calls `error()` from there.

**But the `buildAnalysisGroups` step is currently too late to fail.**
`ch_samplesheet` feeds `ch_validated` (in the `MORPHPORT` workflow, `workflows/morphport.nf`) and the CytoTable processes directly, so by the time the `buildAnalysisGroups` closure runs those tasks have already been submitted.
On AWS Batch that means a typo in the groups file costs real compute before it surfaces.

The recommended implementation is therefore to move the value-dependent checks into the `.map{}` closure that calls `validateInputSamplesheet`.
That closure runs on `.toList()`, already has every resolved `platemap_file`, and sits upstream of the first `ch_samplesheet` emission — nothing has launched yet when it raises.
It needs the platemap combos, currently enumerated later by the `enumeratePlatemapCombos` call in `ch_analysis_groups`, so the implementation has to either thread that result forward or accept enumerating twice.

### Message style

Errors should name the offending entry by its `name` (explicit or auto-generated), since that is what `name` exists for.
Prefer collecting all problems and reporting them together, as `validateInputSamplesheet` does with its `errors` list, rather than failing on the first one — a groups file with three typos should report three.

## Full example

```yaml
default:
  # Every treatment value gets its own analysis group, as today.
  - grouping_column: treatment
    groups: one_per_value

cell:
  # One group per cell line, restricted to wells with no drug treatment.
  # Note that the initial partitions will have to be split by both cell type and treatment
  - name: untreated_cell_lines
    grouping_column: cell_type
    filter:
      treatment: [DMSO]
    groups: one_per_value

  # Grouping by mechanism of action.
  # DMSO is folded into every named group so each MoA can be compared against the shared control.
  # drugE represents a treatment value that has no known MoA yet.
  # It is explicitly excluded so it doesn't trigger an "unaccounted for value" error.
  - name: moa_grouping
    grouping_column: treatment
    groups:
      kinase_inhibitor: [drugA, drugB, drugC]
      hdac_inhibitor: [drugD, drugA] # drugA has two known mechanisms
    always_include: [DMSO]
    excluded: [drugE]

  # MoA groups further split by cell line.
  # For subgroups we always split by all values, which keeps things a bit simpler
  # We could in theory split by more than one subgroup, but practically we only expect one for now
  - name: moa_by_cell_line
    grouping_column: treatment
    groups:
      kinase_inhibitor: [drugA, drugB, drugC]
      hdac_inhibitor: [drugD, drugA]
    always_include: [DMSO]
    excluded: [drugE]
    subgroup_columns: [cell_type]

well:
  # well-level grouping by cell type, added to the default treatment grouping
  - grouping_column: cell_type
    groups: one_per_value
```

## What this format does not (yet) support

- **Negative filters.** `filter` is keep-only; there is no "every value except these" form.
  For the grouping column itself `excluded` covers it, and the motivating filters (control wells, a timepoint) are short enumerable lists.
  A column with many values where the exclusion list is shorter than the inclusion list would make the case for adding one.
