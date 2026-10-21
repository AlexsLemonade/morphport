# Synthetic test data

This directory contains a small synthetic Cell Painting dataset for testing the pipeline.
The plates and their layouts are defined by the samplesheet and platemaps here; the single-cell profiles are randomly generated from them by `scripts/generate_test_data.py`.

The data use the Cell Painting Gallery directory structure and start at the converted parquet stage, so plates use `cytotable_preset: skip`.
There are six plates: two plate layouts (`Assay_Plate1` and `Assay_Plate2`) in one batch (`Round_1`), each imaged at 24, 48, and 72 hours.
Each layout has four cell lines, with U2-OS on both layouts as an anchor, and includes U2-OS wells on each edge of the plate.

- `samplesheet.csv`: samplesheet for all six plates
- `analysis_groups.yml`: analysis groups using the platemap columns
- `test-project/test/workspace/metadata/platemaps/<batch>/`: `barcode_platemap.csv` and per-plate platemaps
- `test-project/test/workspace/profiles/<batch>_<plate_layout>/<plate>/<plate>_converted.parquet`: single-cell profiles (generated)

## Generating the profiles

The parquet files are not tracked by git.
To create them, run the following from the repository root:

```bash
uv run scripts/generate_test_data.py
```

The script simulates cells with random feature values for every well in each plate's platemap.
To change the test data, edit the samplesheet and platemaps, then rerun the script.

## Running the pipeline

The `test` profile uses this samplesheet and analysis groups file.
From the repository root:

```bash
nextflow run . -profile test,docker --outdir results/test-data
```

The `-profile test` nf-test in `tests/default.nf.test` also runs the pipeline on these data.
