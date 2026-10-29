# AlexsLemonade/morphport: Output

## Introduction

This document describes the output produced by the pipeline.

The directories listed below will be created in the results directory after the pipeline has finished.
All paths are relative to the top-level results directory.

## Output directory structure

Outputs are organized by project, then by aggregate level, then by the tool that produced them:

```text
<outdir>/
├── pipeline_info/
└── <project>/
    ├── cell/
    │   ├── cytotable/
    │   ├── pycytominer/
    │   │   ├── <plate_id>_annotated.parquet    (optional)
    │   │   └── edge_wells_[included|excluded]/
    │   ├── cosmicqc/edge_wells_[included|excluded]/
    │   ├── embeddings/edge_wells_[included|excluded]/
    │   └── hierarchical_clustering/edge_wells_[included|excluded]/
    └── well/
        ├── pycytominer/edge_wells_[included|excluded]/
        ├── embeddings/edge_wells_[included|excluded]/
        └── hierarchical_clustering/edge_wells_[included|excluded]/
```

- `<project>` is the `project` value from the samplesheet.
- `cell/` contains single-cell profiles and results computed from them.
- `well/` contains well-level aggregated profiles and results computed from them.
- Outputs from coSMicQC filtering onward are placed in an `edge_wells_included/` or `edge_wells_excluded/` directory within each tool directory, depending on whether the wells on the edge of the plate were removed.
  With `--edge_well_filter off`, only `edge_wells_included/` is present; with `--edge_well_filter on`, only `edge_wells_excluded/` is present.
  With `--edge_well_filter both` (the default), every step from coSMicQC filtering onward is run twice, and both directories are present.
  Steps before coSMicQC filtering (CytoTable and platemap annotation) run once per plate, so their outputs are not placed in these directories.

### File naming

Per-plate outputs (up to and including normalization) are named with the plate identifier (`<plate_id>`) from the samplesheet.

Per-group outputs (feature selection and later) are named with a group tag (`<group_tag>`) that identifies the analysis group, of the form:

```text
<aggregate_level>__<grouping_name>__<group_id>
```

where `<group_id>` lists the grouping column and any subgroup columns with their values, without the `Metadata_` prefix, joined by `__`.
For example, `cell__moa_by_cell_line__treatment=kinase_inhibitor__cell_type=lineA`.
When no analysis groups file is provided, each level has a single group, and the tag is `cell__all` or `well__all`.
Characters other than letters, digits, `.`, `_`, `=`, and `-` are replaced with `_`.
See [the analysis groups file format](analysis_groups_format.md) for how groups are defined.

## Pipeline overview

The pipeline is built using [Nextflow](https://www.nextflow.io/) and processes data using the following steps:

- [CytoTable](#cytotable) - Convert CellProfiler SQLite output to parquet
- [Platemap annotation](#platemap-annotation) - Add platemap metadata to each cell (optional output)
- [coSMicQC](#cosmicqc) - Remove edge wells and outlier cells
- [Aggregation](#aggregation) - Summarize single-cell profiles to well-level profiles
- [Normalization](#normalization) - Normalize profiles within each plate
- [Plate partitioning](#plate-partitioning) - Split plates by experimental condition (optional output)
- [Feature selection](#feature-selection) - Select informative features for each analysis group
- [Embeddings](#embeddings) - Compute PCA and UMAP embeddings for each analysis group
- [Hierarchical clustering](#hierarchical-clustering) - Cluster the samples and features within each analysis group
- [Pipeline information](#pipeline-information) - Report metrics generated during the workflow execution

### CytoTable

<details markdown="1">
<summary>Output files</summary>

- `<project>/cell/cytotable/`
  - `<plate_id>.parquet`: Single-cell profiles for the plate, converted from the CellProfiler SQLite file.

</details>

[CytoTable](https://github.com/cytomining/CytoTable) converts the CellProfiler SQLite database for each plate to a single parquet file, joining the per-compartment tables into one row per cell.
This step only runs for plates with a `sqlite_path` in the samplesheet; plates with `cytotable_preset` set to `skip` use the existing parquet file(s) at `parquet_path` and produce no output here.

### Platemap annotation

<details markdown="1">
<summary>Output files</summary>

- `<project>/cell/pycytominer/`
  - `<plate_id>_annotated.parquet`: Single-cell profiles with platemap metadata added as `Metadata_`-prefixed columns.

</details>

[Pycytominer](https://github.com/cytomining/pycytominer) `annotate` joins the platemap for each plate to its single-cell profiles by well.
These files are intermediate outputs and are only published with `--publish_optional` (or the `debug` profile).

### coSMicQC

<details markdown="1">
<summary>Output files</summary>

- `<project>/cell/cosmicqc/edge_wells_*/`
  - `filtered_<plate_id>.parquet`: Annotated single-cell profiles with edge wells (if excluded) and outlier cells removed.
  - `cosmicqc_outliers_<plate_id>.csv.gz`: One row per outlier cell, with its metadata columns and a `cosmicqc_condition` column listing the filter condition(s) that flagged it, separated by `;`.
    The first column is the cell's row index in the annotated input.
  - `cosmicqc_summary_<plate_id>.json`: Filtering summary for the plate, including:
    - `filter_settings`: the filter conditions used (from `--cosmicqc_filter_file`).
    - `cosmicqc_filtering`: cell counts before and after outlier removal, the fraction of cells removed, the number of outliers found by each condition, and any conditions skipped because their features were not present.
    - `edge_filtering`: whether edge wells were included, the inferred plate size, the edge wells removed, and cell counts before and after edge well removal.

</details>

When edge wells are excluded, wells on the border of the plate are removed first, based on a standard 96-, 384-, or 1536-well layout inferred from the wells present.
[coSMicQC](https://github.com/cytomining/coSMicQC) then flags outlier cells using the feature thresholds in each filter condition, and those cells are removed.

### Aggregation

<details markdown="1">
<summary>Output files</summary>

- `<project>/well/pycytominer/edge_wells_*/`
  - `well_<plate_id>.parquet`: Well-level profiles, one row per well.

</details>

Pycytominer `aggregate` summarizes the filtered single-cell profiles in each well using the operation set by `--pycytominer_aggregate_operation` (`median` or `mean`).

### Normalization

<details markdown="1">
<summary>Output files</summary>

- `<project>/cell/pycytominer/edge_wells_*/`
  - `normalized_cell_<plate_id>.parquet`: Normalized single-cell profiles.
- `<project>/well/pycytominer/edge_wells_*/`
  - `normalized_well_<plate_id>.parquet`: Normalized well-level profiles.

</details>

Pycytominer `normalize` normalizes features within each plate, using the methods set by `--pycytominer_normalize_method_cell` and `--pycytominer_normalize_method_well` and the reference samples set by `--pycytominer_normalize_samples`.
If the normalization method for a level is `none`, normalization is skipped and no normalized files are written for that level.

### Plate partitioning

<details markdown="1">
<summary>Output files</summary>

- `<project>/<level>/partition/edge_wells_*/`
  - `<plate_id>/`: [Hive-style](https://duckdb.org/docs/stable/data/partitioning/hive_partitioning) partitioned parquet files for the plate, with one directory level per partition column (e.g. `Metadata_treatment=drugA/`).
    Values in directory names are percent-encoded.
  - `partitions_<plate_id>.json`: A list of the partition directories, each with its decoded column values.
  - `platemap_summary_<plate_id>.json`: The partition columns and the number of rows for each value of each column.

</details>

Each plate is split by the metadata columns used in the analysis groups file, so that partitions can be combined into analysis groups across plates.
This step only runs when an analysis groups file is provided.
These files are intermediate outputs and are only published with `--publish_optional` (or the `debug` profile).

### Feature selection

<details markdown="1">
<summary>Output files</summary>

- `<project>/<level>/pycytominer/edge_wells_*/`
  - `features_<group_tag>.txt`: The selected feature names, one per line.
  - `feature_select_summary_<group_tag>.json`: Feature selection summary for the group, including the group definition, the operations requested and applied, the parameters used, the imaging channels detected from feature names, and the number of features before and after selection.

</details>

Pycytominer `feature_select` is run on all of the profiles in each analysis group, using the operations set by `--feature_select_operations`.
Statistics-based operations are skipped for groups with fewer rows than `--feature_select_min_rows_stats`; the summary records which operations were applied.
With `--feature_select_per_channel_correlation`, the correlation threshold is applied separately within each set of features that share imaging channels, and the summary includes per-channel feature counts.
No filtered profiles are written; downstream steps read the normalized profiles and keep only the selected features.
Groups with no selected features are skipped by later steps, with a warning in the run log.

### Embeddings

<details markdown="1">
<summary>Output files</summary>

- `<project>/<level>/embeddings/edge_wells_*/`
  - `embeddings_<group_tag>.parquet`: One row per sample (cell or well) in the group, with all `Metadata_` columns, principal components (`PC1`, `PC2`, ...), and UMAP coordinates (`UMAP1`, `UMAP2`).
  - `embeddings_summary_<group_tag>.json`: Embedding summary for the group, including the group definition, the source plates, row counts before and after NaN handling, and the PCA (components used, explained variance ratios) and UMAP (neighbors, minimum distance, random seed) settings.

</details>

PCA is computed on the selected features, and UMAP is then computed from the principal components.
Rows with a fraction of missing feature values greater than `--sample_max_nan_fraction` are dropped; remaining missing values are set to 0.
The number of principal components and UMAP neighbors are reduced if the group is too small for the requested values; the summary records the values used.
If fewer than `--embedding_min_rows` rows remain, the group is skipped: the parquet file contains only metadata columns, and the summary has `skipped: true` and a `skip_reason`.

### Hierarchical clustering

<details markdown="1">
<summary>Output files</summary>

- `<project>/<level>/hierarchical_clustering/edge_wells_*/`
  - `clustering_samples_linkage_<group_tag>.parquet`: The [scipy linkage matrix](https://docs.scipy.org/doc/scipy/reference/generated/scipy.cluster.hierarchy.linkage.html), one row per merge, with columns `left`, `right`, `distance`, and `n_members`.
    Following scipy, leaves are numbered `0` to `n-1`, and the node created by row `i` is numbered `n + i`.
    The tree can be rebuilt with `scipy.cluster.hierarchy.to_tree()`.
  - `clustering_samples_leaves_<group_tag>.parquet`: One row per leaf (sample), with columns `leaf_id` (the row number in the clustered matrix, after dropping rows with missing values), `leaf_order` (position in the dendrogram), `label`, and the grouping, subgroup, and sample identifier metadata columns.
  - `clustering_samples_summary_<group_tag>.json`: Sample clustering summary for the group, including the group definition, clustering input, linkage method, distance metric, NaN filtering counts, and number of samples.
  - `clustering_features_linkage_<group_tag>.parquet`: The scipy linkage matrix for the selected features, in the same format as the sample linkage matrix.
  - `clustering_features_leaves_<group_tag>.parquet`: One row per leaf (feature), with columns `leaf_id` (the row number in the linkage matrix), `leaf_order` (position in the dendrogram), and `label` (the feature name).
  - `clustering_features_summary_<group_tag>.json`: Feature clustering summary for the group, including the group definition, linkage method, distance metric, NaN filtering counts, and number of samples and features.

</details>

Samples in each group are clustered with Ward linkage, using [fastcluster](https://danifold.net/fastcluster.html)'s memory-saving algorithm so that large cell-level groups can be clustered without computing all pairwise distances at once.
By default, samples are clustered on their selected features; set `--clustering_samples_input pcs` to cluster on their principal components instead, which is faster for large groups.
The distance metric is set by `--clustering_samples_distance_metric`.
Because Ward linkage requires euclidean distances, the `correlation` metric is implemented by centering each sample and scaling it to unit length, and `cosine` by scaling each sample to unit length.
For these metrics, the merge distances in the linkage matrix are on the euclidean scale of the transformed samples, where the squared distance between two samples is `2 * (1 - r)` for correlation (or cosine similarity) `r`.
As in the embedding step, rows with a fraction of missing values above `--sample_max_nan_fraction` are dropped, and remaining missing values are filled with 0.
Groups are skipped if they were skipped by the embedding step (when clustering principal components), have fewer than 2 samples, or have more than `--clustering_max_samples` samples.

The selected features in each group are clustered separately, using the feature-selected profiles of all samples in the group, with the linkage method and distance metric set by `--clustering_features_linkage_method` and `--clustering_features_distance_metric`.
Features are not scaled before clustering.
As in the embedding step, rows with a fraction of missing feature values above `--sample_max_nan_fraction` are dropped, and remaining missing values are filled with 0.
Constant features should already be removed by feature selection; as a backup, any features that are constant across the remaining samples are excluded.
Feature clustering is run for all groups, including cell-level groups, unless fewer than 2 samples or 2 features remain after filtering.

For skipped groups, the parquet files are empty, and the summary has `skipped: true` and a `skip_reason`.

### Pipeline information

<details markdown="1">
<summary>Output files</summary>

- `pipeline_info/`
  - Reports generated by Nextflow: `execution_report_<timestamp>.html`, `execution_timeline_<timestamp>.html`, `execution_trace_<timestamp>.txt` and `pipeline_dag_<timestamp>.html`.
  - Reports generated by the pipeline: `pipeline_report.html`, `pipeline_report.txt` and `morphport_software_versions.yml`.
    The `pipeline_report*` files will only be present if the `--email` / `--email_on_fail` parameters are used when running the pipeline.
  - Parameters used by the pipeline run: `params_<timestamp>.json`.
  - Warnings about the input data raised during the run (e.g. conflicting metadata columns found during platemap annotation): `morphport_warnings_<timestamp>.txt`.
    Each line gives the process, the plate, and the warning.
    The same warnings are printed in the run log.
    This file is only present if there were any warnings.

</details>

[Nextflow](https://docs.seqera.io/platform-cloud/reports/overview) provides excellent functionality for generating various reports relevant to the running and execution of the pipeline.
This will allow you to troubleshoot errors with the running of the pipeline, and also provide you with other information such as launch commands, run times and resource usage.
