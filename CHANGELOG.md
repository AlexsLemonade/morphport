# AlexsLemonade/morphport: Changelog

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/)
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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

### `Fixed`

### `Dependencies`

### `Deprecated`
