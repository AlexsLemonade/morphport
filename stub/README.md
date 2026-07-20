This directory includes stub files for testing the logic of the pipeline without running process code.

The input files are defined in the samplesheet.csv file in this directory, which refers to stub files in the `test-project` subdirectory.
The stub files are largely empty, but have minimal data when required for for testing process logic.
For example, the metadata `barcode_platemap.csv` file contains information to identify platemaps for each of the stub test plates.

The files in the samplesheet include tests for the following input conditions:

- plate01: sqlite input file, platemap specified indirectly via a `platemap_dir` and the `barcode_platemap.csv` metadata file found there
- plate02: parquet input file, platemap specified directly via the `platemap_file` column in the samplesheet
