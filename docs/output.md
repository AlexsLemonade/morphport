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
    │   ├── cosmicqc/
    │   ├── embeddings/
    │   └── hierarchical_clustering/
    └── well/
        ├── pycytominer/
        ├── embeddings/
        ├── hierarchical_clustering/
        ├── reference_normalization/
        └── phenotypic_activity/
```

- `<project>` is the `project` value from the samplesheet.
- `cell/` contains single-cell profiles and results computed from them.
- `well/` contains per-well profiles (aggregated from the per-cell profiles) and results computed from them.
- Wells on the edge of the plate are removed before coSMicQC filtering unless `--edge_well_filter off` is set; the coSMicQC summary records which wells were removed.
  To compare results with and without edge wells, run the pipeline twice with different output directories.

### File naming

Per-plate outputs (up to and including normalization) are named with the plate identifier (`<plate_id>`) from the samplesheet.

Per-group outputs (feature selection and later) are named with a group tag (`<group_tag>`) that identifies the analysis group, of the form:

```text
<aggregate_level>__<grouping_name>__<group_id>
```

where `<group_id>` lists the grouping column and any subgroup columns with their values, without the `Metadata_` prefix, joined by `__`.
For example, `cell__moa_by_cell_line__treatment=kinase_inhibitor__cell_type=lineA`.
When no analysis groups file is provided, each aggregate level has a single group, and the tag is `cell__all` or `well__all`.
Characters other than letters, digits, `.`, `_`, `=`, and `-` are replaced with `_`.
See [the analysis groups file format](analysis_groups_format.md) for how groups are defined.

## Pipeline overview

The pipeline is built using [Nextflow](https://www.nextflow.io/) and processes data using the following steps:

- [CytoTable](#cytotable) - Convert CellProfiler SQLite output to parquet
- [Platemap annotation](#platemap-annotation) - Add platemap metadata to each cell (optional output)
- [coSMicQC](#cosmicqc) - Remove edge wells and outlier cells
- [Aggregation](#aggregation) - Summarize per-cell profiles to per-well profiles
- [Normalization](#normalization) - Normalize profiles within each plate
- [Plate partitioning](#plate-partitioning) - Split each plate's profiles into partitions (optional output)
- [Feature selection](#feature-selection) - Select informative features for each analysis group
- [Embeddings](#embeddings) - Compute PCA and UMAP embeddings for each analysis group
- [Hierarchical clustering](#hierarchical-clustering) - Cluster the profiles and features within each analysis group
- [Reference normalization](#reference-normalization) - Normalize per-well profiles to each group's reference wells
- [Phenotypic activity](#phenotypic-activity) - Score whether each test condition differs from the reference wells
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

- `<project>/cell/cosmicqc/`
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

- `<project>/well/pycytominer/`
  - `well_<plate_id>.parquet`: Per-well profiles, one row per well.

</details>

Pycytominer `aggregate` summarizes the filtered single-cell profiles in each well using the operation set by `--pycytominer_aggregate_operation` (`median` or `mean`).

### Normalization

<details markdown="1">
<summary>Output files</summary>

- `<project>/cell/pycytominer/`
  - `normalized_cell_<plate_id>.parquet`: Normalized single-cell profiles.
- `<project>/well/pycytominer/`
  - `normalized_well_<plate_id>.parquet`: Normalized per-well profiles.

</details>

Plate normalization (pycytominer `normalize`) normalizes features within each plate, using the methods set by `--pycytominer_plate_normalize_method_cell` and `--pycytominer_plate_normalize_method_well` and the reference profiles set by `--pycytominer_plate_normalize_reference_profiles`.
If the normalization method for an aggregate level is `none`, normalization is skipped and no normalized files are written for that aggregate level.

### Plate partitioning

<details markdown="1">
<summary>Output files</summary>

- `<project>/<aggregate_level>/partition/`
  - `<plate_id>/`: [Hive-style](https://duckdb.org/docs/stable/data/partitioning/hive_partitioning) partitioned parquet files for the plate, with one directory level per partition column (e.g., `Metadata_treatment=drugA/`).
    Values in directory names are percent-encoded.
  - `partitions_<plate_id>.json`: A list of the partition directories, each with its decoded column values.
  - `platemap_summary_<plate_id>.json`: The partition columns and the number of rows for each value of each column.

</details>

Each plate is partitioned by the metadata columns used in the analysis groups file, so that partitions can be combined into analysis groups across plates.
This step only runs when an analysis groups file is provided.
These files are intermediate outputs and are only published with `--publish_optional` (or the `debug` profile).

### Feature selection

<details markdown="1">
<summary>Output files</summary>

- `<project>/<aggregate_level>/pycytominer/`
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

- `<project>/<aggregate_level>/embeddings/`
  - `embeddings_<group_tag>.parquet`: One row per profile (cell or well) in the group, with all `Metadata_` columns, principal components (`PC1`, `PC2`, ...), and UMAP coordinates (`UMAP1`, `UMAP2`).
  - `embeddings_summary_<group_tag>.json`: Embedding summary for the group, including the group definition, the source plates, row counts before and after NaN handling, and the PCA (components used, explained variance ratios) and UMAP (neighbors, minimum distance, random seed) settings.

</details>

PCA is computed on the selected features, and UMAP is then computed from the principal components.
Rows with a fraction of missing feature values greater than `--profile_max_nan_fraction` are dropped; remaining missing values are set to 0.
The number of principal components and UMAP neighbors are reduced if the group is too small for the requested values; the summary records the values used.
If fewer than `--embedding_min_rows` rows remain, the group is skipped: the parquet file contains only metadata columns, and the summary has `skipped: true` and a `skip_reason`.

### Hierarchical clustering

<details markdown="1">
<summary>Output files</summary>

- `<project>/<aggregate_level>/hierarchical_clustering/`
  - `clustering_profiles_linkage_<group_tag>.parquet`: The [scipy linkage matrix](https://docs.scipy.org/doc/scipy/reference/generated/scipy.cluster.hierarchy.linkage.html), one row per merge, with columns `left`, `right`, `distance`, and `n_members`.
    Following scipy, leaves are numbered `0` to `n-1`, and the node created by row `i` is numbered `n + i`.
    The tree can be rebuilt with `scipy.cluster.hierarchy.to_tree()`.
  - `clustering_profiles_leaves_<group_tag>.parquet`: One row per leaf (profile), with columns `leaf_id` (the row number in the clustered matrix, after dropping rows with missing values), `leaf_order` (position in the dendrogram), `label`, and the grouping, subgroup, and profile identifier metadata columns.
  - `clustering_profiles_summary_<group_tag>.json`: Profile clustering summary for the group, including the group definition, clustering input, linkage method, distance metric, NaN filtering counts, and number of profiles.
  - `clustering_features_linkage_<group_tag>.parquet`: The scipy linkage matrix for the selected features, in the same format as the profile linkage matrix.
  - `clustering_features_leaves_<group_tag>.parquet`: One row per leaf (feature), with columns `leaf_id` (the row number in the linkage matrix), `leaf_order` (position in the dendrogram), and `label` (the feature name).
  - `clustering_features_summary_<group_tag>.json`: Feature clustering summary for the group, including the group definition, linkage method, distance metric, NaN filtering counts, and number of profiles and features.

</details>

Profiles in each group are clustered with Ward linkage, using [fastcluster](https://danifold.net/fastcluster.html)'s memory-saving algorithm so that large analysis groups of per-cell profiles can be clustered without computing all pairwise distances at once.
By default, profiles are clustered on their principal components; set `--clustering_profiles_input features` to cluster on their selected features instead, which is slower for large groups since clustering time grows with the number of columns.
The distance metric is set by `--clustering_profiles_distance_metric` (default `cosine`).
The `correlation` metric (Pearson correlation between profiles) is recommended only when clustering features, since centering a profile's principal component scores is not meaningful.
Because Ward linkage requires euclidean distances, the `correlation` metric is implemented by centering each profile and scaling it to unit length, and `cosine` by scaling each profile to unit length.
For these metrics, the merge distances in the linkage matrix are on the euclidean scale of the transformed profiles, where the squared distance between two profiles is `2 * (1 - r)` for correlation (or cosine similarity) `r`.
As in the embedding step, rows with a fraction of missing values above `--profile_max_nan_fraction` are dropped, and remaining missing values are filled with 0.
Groups are skipped if they were skipped by the embedding step (when clustering principal components), have fewer than 2 profiles, or have more than `--clustering_max_profiles` profiles.

The selected features in each group are clustered separately, using all the feature-selected profiles in the group, with the linkage method and distance metric set by `--clustering_features_linkage_method` and `--clustering_features_distance_metric`.
Features are not scaled before clustering.
As in the embedding step, rows with a fraction of missing feature values above `--profile_max_nan_fraction` are dropped, and remaining missing values are filled with 0.
Constant features should already be removed by feature selection; as a backup, any features that are constant across the remaining profiles are excluded.
Feature clustering is run for all groups, including analysis groups of per-cell profiles, unless fewer than 2 profiles or 2 features remain after filtering.

For skipped groups, the parquet files are empty, and the summary has `skipped: true` and a `skip_reason`.

### Reference normalization

<details markdown="1">
<summary>Output files</summary>

- `<project>/well/reference_normalization/`
  - `reference_normalized_features_<group_tag>.txt`: The features kept after reference normalization, one per line.
  - `reference_normalization_summary_<group_tag>.json`: Reference normalization summary for the group, including the group definition, the reference normalization method, the comparison column and reference, the columns that define the normalization sets, the number of wells and reference wells in each normalization set, the wells and features dropped, the plate normalization settings, and whether the group was skipped.

</details>

Per-well analysis groups whose grouping sets a `comparison_column` and `reference_values` are normalized to their reference wells, using pycytominer `normalize` with the method set by `--pycytominer_reference_normalize_method` (default `mad_robustize`).
This is applied to the plate-normalized profiles, keeping only the features selected for the group.
Each plate is normalized separately, and within each plate, each combination of values of the grouping, subgroup, and comparison columns (other than the reference column) is a normalization set, normalized to its own reference wells.
For example, when a group pools several cell types, each cell type is normalized to its own DMSO wells on each plate.
After normalization, each well describes its change from the reference wells on its plate.

- Groups with no reference wells are skipped before this step, with a warning in the run log.
- Normalization sets with fewer than 2 reference wells are dropped, with a warning; a warning is also given for sets with fewer than 5 reference wells.
- Features with no spread among the reference wells of any normalization set are dropped, since they cannot be scaled.
- If no reference wells or no features remain, the group is skipped: the summary has `skipped: true` and a `skip_reason`, and the group is not scored for phenotypic activity.

The reference-normalized profiles are not published, as they are only used by the phenotypic activity step.

### Phenotypic activity

<details markdown="1">
<summary>Output files</summary>

- `<project>/well/phenotypic_activity/`
  - `phenotypic_activity_<group_tag>.parquet`: One row per test condition scored, with columns:
    - `Metadata_<comparison column>`: the test condition (e.g., the treatment).
    - `n_wells`, `n_plates`: the number of wells scored for the test condition and the number of plates they are spread across.
    - `n_reference_wells`: the number of reference wells its wells were compared with.
    - `mean_average_precision`: the mean average precision (mAP).
    - `p_value`, `corrected_p_value`: the p-value for the mAP, and the p-value corrected for the number of test conditions in the group (Benjamini-Hochberg).
    - `active`: whether the test condition is called as active.
  - `phenotypic_activity_summary_<group_tag>.json`: Phenotypic activity summary for the group, including the group definition, the comparison column and reference, the scoring parameters, the copairs version, the columns used to match pairs of wells, well and feature counts before and after filtering, the number of test conditions scored and called as active, the reference normalization, plate normalization, and edge well settings, and whether the group was skipped.

</details>

Phenotypic activity measures whether the wells of each test condition (e.g., a treatment) are more similar to each other than to the reference wells (e.g., DMSO), using mean average precision (mAP) from [copairs](https://github.com/cytomining/copairs), on the reference-normalized profiles.
For each well of a test condition, the other wells of that test condition and the reference wells are ranked by similarity, using the distance metric set by `--phenotypic_activity_distance_metric` (default `cosine`).
Average precision measures how highly the wells of the same test condition rank, and the mAP is the mean across the test condition's wells.
Wells are only compared with wells that have the same values of the grouping, subgroup, and comparison columns other than the reference column, as in reference normalization.

Each mAP is tested against a null distribution of `--phenotypic_activity_null_size` draws (default 1,000,000), using the null method set by `--phenotypic_activity_null` (default `permutation`) and seeded with `--random_seed`.
A test condition is called as active (`active` is `true`) when its corrected p-value is below `--phenotypic_activity_fdr_threshold` (default 0.05).
Since the smallest possible p-value depends on the null size, a small null size can mean no test condition can be called as active; a warning is given when this happens.

Activity is computed separately for each analysis group, with the group's selected features and with the correction applied across the group's test conditions.
A treatment that appears in more than one group, such as a group of treatments within one cell type and a group of cell types within that treatment, can therefore get different results in each.
For a single result per treatment and cell type, read activity from groups that compare treatments within one cell type.

Before scoring, wells with a missing comparison column value are dropped, as are the wells of a test condition in any cell type where it has fewer than `--consensus_min_wells` wells (at least 2).
Features with any missing values in the remaining wells are dropped.
If no reference wells or test conditions remain, the group is skipped: the parquet file has no rows, and the summary has `skipped: true` and a `skip_reason`.

### Pipeline information

<details markdown="1">
<summary>Output files</summary>

- `pipeline_info/`
  - Reports generated by Nextflow: `execution_report_<timestamp>.html`, `execution_timeline_<timestamp>.html`, `execution_trace_<timestamp>.txt` and `pipeline_dag_<timestamp>.html`.
  - Reports generated by the pipeline: `pipeline_report.html`, `pipeline_report.txt` and `morphport_software_versions.yml`.
    The `pipeline_report*` files will only be present if the `--email` / `--email_on_fail` parameters are used when running the pipeline.
  - Parameters used by the pipeline run: `params_<timestamp>.json`.
  - Warnings about the input data raised during the run (e.g., conflicting metadata columns found during platemap annotation): `morphport_warnings_<timestamp>.txt`.
    Each line gives the process, the plate, and the warning.
    The same warnings are printed in the run log.
    This file is only present if there were any warnings.

</details>

[Nextflow](https://docs.seqera.io/platform-cloud/reports/overview) provides excellent functionality for generating various reports relevant to the running and execution of the pipeline.
This will allow you to troubleshoot errors with the running of the pipeline, and also provide you with other information such as launch commands, run times and resource usage.
