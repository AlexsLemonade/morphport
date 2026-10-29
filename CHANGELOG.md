# AlexsLemonade/morphport: Changelog

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/)
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## v0.1.1dev - [unreleased<!-- TODO nf-core: replace with date on release -->]

### `Added`

- `HIERARCHICAL_CLUSTERING_FEATURES`: clusters the selected features within each analysis group, using the feature-selected profiles of all samples in the group.
  Outputs are written to `hierarchical_clustering/` as `clustering_features_linkage_*.parquet`, `clustering_features_leaves_*.parquet`, and `clustering_features_summary_*.json`.
  The linkage method and distance metric are set with the new `--clustering_features_linkage_method` and `--clustering_features_distance_metric` parameters.

### `Changed`

- `HIERARCHICAL_CLUSTERING` is renamed to `HIERARCHICAL_CLUSTERING_SAMPLES`, and its outputs are renamed to `clustering_samples_linkage_*.parquet`, `clustering_samples_leaves_*.parquet`, and `clustering_samples_summary_*.json`, to distinguish them from the feature clustering outputs.
- `--clustering_linkage_method` and `--clustering_distance_metric` are renamed to `--clustering_samples_linkage_method` and `--clustering_samples_distance_metric`.
- `HIERARCHICAL_CLUSTERING_SAMPLES` now always uses Ward linkage via fastcluster's memory-saving `linkage_vector`, to support clustering large cell-level groups.
  Samples are clustered on their principal components by default; the new `--clustering_samples_input features` option clusters on the selected features instead.
  `--clustering_samples_linkage_method` is removed, and `--clustering_samples_distance_metric` now accepts `cosine` (default), `correlation`, or `euclidean`.
  The default `--clustering_max_samples` is raised from 5000 to 200000.
- `--embedding_max_nan_fraction` is renamed to `--sample_max_nan_fraction`, since it is shared by the embedding and clustering steps.

### `Fixed`

### `Dependencies`

- Updated to nf-core template 4.2.1

### `Deprecated`

## v0.1.0 - [2026-10-27]

Initial release of `AlexsLemonade/morphport`, created with the [nf-core](https://nf-co.re/) template.

### `Added`

Implemented the core morphport workflow for downstream analysis of Cell Painting data, preparing single-cell imaging profiles for the PCCMA portal.
The pipeline takes per-plate CellProfiler outputs through conversion, quality control, aggregation, and analysis steps, including the following modules:

- `CYTOTABLE_CONVERT`: converts raw per-plate CellProfiler output to parquet.
- `PYCYTOMINER_ANNOTATE`: annotates cells with platemap metadata.
- `COSMICQC_FILTER`: applies coSMicQC quality filtering, with optional edge-well removal.
- `PYCYTOMINER_AGGREGATE`: aggregates cell-level profiles to well-level profiles.
- `PYCYTOMINER_NORMALIZE`: normalizes aggregated profiles.
- `PARTITION_CONDITIONS`: partitions profiles by experimental condition into Hive-style directories.
- `PYCYTOMINER_FEATURE_SELECT`: performs feature selection across configurable criteria.
- `COMPUTE_EMBEDDINGS`: computes PCA and UMAP embeddings.
- `HIERARCHICAL_CLUSTERING`: performs hierarchical clustering on the resulting embeddings.

Analyses performed after partitioning (feature selection, embeddings, clustering, etc.) are run on groups of experimental conditions defined by an analysis groups file (`--analysis_groups_file`); see `docs/analysis_groups_format.md`.
