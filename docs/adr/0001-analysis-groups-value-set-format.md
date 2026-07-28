# Redesign the analysis groups file format around explicit, fan-out value-sets

**Status**: accepted

We needed analysis groups that don't map 1:1 to a single metadata value.
For example, grouping drugs by mechanism of action (MoA), where a drug can have more than one MoA, and where control samples like DMSO need to join every group rather than forming their own.
The existing format (`assets/analysis_groups_example.yaml`) only supports "one group per distinct value (or value-combination) observed in the data," with no way to name a group as an explicit set of values, and no way for one sample to belong to more than one group.

We decided to:

- Redesign each grouping entry as an object (`grouping_column`, `groups`, `always_include`, `excluded`, optional `name`), rather than extend the existing bare-string / comma-separated-column shorthand.
  Since the pipeline is pre-release (`v0.1.0dev`, no installed base), this is a clean breaking change rather than an additive one.
- Require `groups:` on every entry, with no implicit default.
  The sentinel `one_per_value` spells out today's only behavior explicitly; the alternative is a mapping of group name → explicit value-set.
  This trades a small amount of verbosity for never having a reader guess "what does an entry with no `groups:` key do?"
- Allow a sample's data to belong to more than one analysis group (fan-out) — necessary for both multi-MoA drugs and shared controls.
  `createGroupChannels()`'s `flatMap` already emits one tuple per matching grouping; fan-out is a bounded extension of that, not a redesign.
- Restrict `grouping_column` to a single column per entry (dropping today's multi-column combo grouping, e.g. `treatment,cell_type`), to avoid the added complexity of expressing `always_include`/`excluded` as value-tuples instead of flat value lists.
  Partitioning underneath is unaffected — it still shards by the union of every entry's `grouping_column`, so cross-entry reuse of fine-grained partitions is preserved.
- Error (not warn) when a named-group entry has an observed value that isn't in any named group, `always_include`, or `excluded`.
  Exploratory, unreviewed groupings from a silent drop or a mere warning risk masking a typo'd or newly-added drug name with no group membership and no visible signal; `excluded` exists precisely so a deliberately-unmapped value doesn't need to trigger this error.

## Considered options

- **Extend the old string format additively** (e.g. treat a mapping value as "named groups" and keep the comma-string form for plain column shards) — rejected because it leaves two different implicit conventions live at once, and there's no installed base forcing us to preserve the old shape.
- **Warn instead of error on unaccounted-for values** — rejected in favor of a required, explicit accounting (via `excluded`) specifically because it removes any ambiguity between "we forgot this value" and "we know about this value and it's fine to skip."
- **Multi-column value-sets** (a named group defined by value-sets across more than one column) — deferred; nothing in the motivating use cases (MoA/target mapping, shared controls) needs it, and it would require value-tuples instead of flat value lists throughout `groups`/`always_include`/`excluded`.

## Consequences

- The comma-separated multi-column shorthand (`treatment,cell_type`) is gone.
  Anyone wanting the old "one group per (treatment, cell_type) pair" behavior needs two single-column entries instead, which is not equivalent (two sets of groups, not one group per combo) — there is currently no replacement for true multi-column grouping.
- This format is documented and exemplified (`docs/analysis_groups_format.md`, a new unwired example file) without changing the parser, `nextflow_schema.json`, or the runtime fixtures (`assets/analysis_groups_example.yaml`, `stub/analysis_groups.yml`) — those changes land together with the parser rewrite in the follow-up implementation work.
