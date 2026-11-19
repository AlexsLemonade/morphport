This directory includes stub files for testing the logic of the pipeline without running process code.

The input files are defined in the samplesheet.csv file in this directory, which refers to stub files in the `test-project` subdirectory.
The stub files are largely empty, but have minimal data when required for for testing process logic.
For example, the metadata `barcode_platemap.csv` file contains information to identify platemaps for each of the stub test plates.

The files in the samplesheet include tests for the following input conditions:

- plate01: sqlite input file, platemap specified indirectly via a `platemap_dir` and the `barcode_platemap.csv` metadata file found there
- plate02: parquet input file, platemap specified directly via the `platemap_file` column in the samplesheet

The stub `PARTITION_PROFILES` step reads each plate's platemap, so the analysis groups in `analysis_groups.yml` are formed from the stub platemap values as in a real run.
The analysis groups include the following conditions:

- `stub_condition` and `stub_target`: grouping columns missing from both platemaps, so each plate gets the value `_Absent_`
- `allele_vs_wt`: per-well groups with reference wells, which run reference normalization and phenotypic activity
- `platemap_by_allele`: per-well groups where one group (`gene_allele=mut`) has no reference wells, so it skips reference normalization and phenotypic activity
