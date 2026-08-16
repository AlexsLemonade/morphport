# Morphport Analysis Grouping

Domain vocabulary for how morphport partitions and groups profiles into analysis units before feature selection, embeddings, and clustering.

## Language

**Aggregate level**:
Either `cell` or `well` — the granularity at which profiles are grouped for analysis.
Corresponds to the two pipeline stages (post-`COSMICQC_FILTER` and post-`PYCYTOMINER_AGGREGATE`) that get grouped and analyzed independently.
_Avoid_: level, stage

**Analysis group**:
A set of profiles, drawn from one or more partitions within the same project, that gets analyzed together by `PYCYTOMINER_FEATURE_SELECT`, `COMPUTE_EMBEDDINGS`, and `HIERARCHICAL_CLUSTERING`.
Each analysis group is defined by a grouping entry's grouping column together with either a single observed value (the default) or an explicit set of values.
A sample (partition) can belong to more than one analysis group — e.g. a drug with two mechanisms of action, or a control included in every group.
_Avoid_: group, cohort, value-set group

**Grouping column**:
The single metadata column (`Metadata_`-prefixed) named by a grouping entry's `grouping_column` key in the analysis groups file.
An entry's grouping column, combined with its `groups:` value (either `one_per_value` or an explicit mapping of group name to value-set), determines which partitions become which analysis groups.
Restricted to exactly one column per entry; multi-column combo grouping is deferred.
_Avoid_: grouping columns, columns, group_by

**`one_per_value`**:
The `groups:` sentinel meaning "one analysis group per distinct value observed in the data for this grouping column."
The explicit spelling of what used to be the only, implicit behavior.
_Avoid_: each_observed_value, auto, default grouping

**Partition column**:
Any metadata column named as a `grouping_column` by any entry, across every level and `default`.
The union of all partition columns is what `PARTITION_CONDITIONS` actually splits plate data by — a finer granularity than any single grouping entry needs, so that fine-grained partitions can be shared and recombined across multiple, differently-grouped entries.
_Avoid_: condition column
