# AlexsLemonade/morphport: Changelog

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/)
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## v0.1.1dev - [unreleased<!-- TODO nf-core: replace with date on release -->]

### `Added`

- `HIERARCHICAL_CLUSTERING_FEATURES`: clusters the selected features within each analysis group, using all the feature-selected profiles in the group.
  Outputs are written to `hierarchical_clustering/` as `clustering_features_linkage_*.parquet`, `clustering_features_leaves_*.parquet`, and `clustering_features_summary_*.json`.
  The linkage method and distance metric are set with the new `--clustering_features_linkage_method` and `--clustering_features_distance_metric` parameters.

### `Changed`

- `HIERARCHICAL_CLUSTERING` is renamed to `HIERARCHICAL_CLUSTERING_PROFILES`, and its outputs are renamed to `clustering_profiles_linkage_*.parquet`, `clustering_profiles_leaves_*.parquet`, and `clustering_profiles_summary_*.json`, to distinguish them from the feature clustering outputs.
  The `n_samples` and `n_samples_zero_length` keys in the clustering summary files are renamed to `n_profiles` and `n_profiles_zero_length`.
- `--clustering_linkage_method` and `--clustering_distance_metric` are renamed to `--clustering_profiles_linkage_method` and `--clustering_profiles_distance_metric`.
- `HIERARCHICAL_CLUSTERING_PROFILES` now always uses Ward linkage via fastcluster's memory-saving `linkage_vector`, to support clustering large analysis groups of per-cell profiles.
  Profiles are clustered on their principal components by default; the new `--clustering_profiles_input features` option clusters on the selected features instead.
  `--clustering_profiles_linkage_method` is removed, and `--clustering_profiles_distance_metric` now accepts `cosine` (default), `correlation`, or `euclidean`.
  `--clustering_max_samples` is renamed to `--clustering_max_profiles`, and its default is raised from 5000 to 200000.
- `--embedding_max_nan_fraction` is renamed to `--profile_max_nan_fraction`, since it is shared by the embedding and clustering steps.
- `PARTITION_CONDITIONS` is renamed to `PARTITION_PROFILES`.
- `--pycytominer_normalize_samples` is renamed to `--pycytominer_normalize_reference_profiles`.

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
