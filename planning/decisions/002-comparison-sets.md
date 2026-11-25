---
Status: accepted (partly supersedes Decision-001)
---

# Score and summarize test conditions once per comparison set

Phenotypic activity is currently scored per analysis group, with per-group feature selection and a per-group multiple-testing family, so a treatment that appears in several groups (e.g. all compounds vs. DMSO, and the compounds of one mechanism of action vs. DMSO) gets a different score in each, and sometimes a different activity call.
In the simulated REPO1 screen, mAP scores agreed closely between those two groupings (correlation 0.998), but two calls near the threshold differed, from feature selection and correction alone.
We decided that a test condition is scored once, in its **comparison set** (see `docs/GLOSSARY.md`), and that analysis groups are used only downstream, to select test conditions for embeddings, clustering, and consensus similarity.

- A comparison set is fixed by the comparison column, the comparison reference, and the raw values of the comparison set columns (grouping column, unless it is the comparison column, and subgroup columns) within a project.
  Groupings that share these share comparison sets; comparing in the other direction (cell types within each treatment) gives separate comparison sets, which may score differently.
- A comparison set holds the union of the test conditions its groupings include; filters, named groups, and `excluded` only select test conditions, and a filter on a column that varies within a test condition (e.g. time point) is an error.
- Reference normalization stays per plate and per cell type, as in Decision-001, but runs once per comparison set rather than once per analysis group.
- Feature selection runs once per comparison (across all of a project's comparison sets for one comparison column and reference), so any analysis group, including one spanning several comparison sets (e.g. a group of cell types), has one feature set, and a test condition has the same profile in every group.
- Phenotypic activity is scored and corrected per comparison set, and consensus profiles are built once per test condition; analysis groups produce no activity tables of their own and slice consensus similarity.
- A grouping key, `annotation_columns`, carries platemap columns (e.g. mechanism of action) into the per-test-condition outputs, so results can be sliced by them.
- Comparisons without a reference follow the same model without reference normalization and activity; per-cell groupings are unchanged.

This replaces Decision-001's per-analysis-group reference normalization; its reasons for a separate reference normalization step, and its per-cell-type reference matching, still hold.

## Considered options

- **Keep scoring per analysis group** and document that results can differ between groups (the previous plan): simplest, but the same wells get different answers depending on how groups are defined.
- **An explicit key naming the grouping that scores** (e.g. `scored_by`): clear, but extra configuration for what can be derived from the comparison column, reference, and comparison set columns.
- **Score every value of the comparison column**, regardless of the groupings' filters: comparison sets would not change when filters change, but values that no grouping wants (e.g. excluded compounds) would still be scored.
- **Feature selection per comparison set, or per analysis group for downstream analyses**: groups spanning cell types would lack a common feature set, or a test condition's consensus profile would again depend on the group.

## Consequences

- A comparison set's membership depends on its groupings, so adding a grouping that includes more test conditions, or loosening a filter, changes the set's feature selection and multiple-testing family and can shift other test conditions' scores slightly.
- Activity and consensus results are no longer duplicated per analysis group; consumers slice the per-comparison-set outputs using the annotation columns.
- The phenotypic activity spec's guidance that activity may differ between groups, and the consensus similarity design (per-group consensus profiles and feature selection), need revising.
