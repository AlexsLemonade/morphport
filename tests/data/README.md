# Synthetic test data

This directory contains a small synthetic Cell Painting dataset for testing the pipeline.
The plates and their layouts are defined by the samplesheet and platemaps here; the single-cell profiles are randomly generated from them by `scripts/generate_test_data.py`.
The data are designed to exercise each analysis step with as few tasks as possible, not to be realistic.

The data use the Cell Painting Gallery directory structure and start at the converted parquet stage, so plates use `cytotable_preset: skip`.
There are two plates (`plate_1` and `plate_2`) in one batch (`batch_1`), with the same layout (`platemap_A`).
`plate_1` finds its platemap through `platemaps_dir` and `barcode_platemap.csv`; `plate_2` names it directly with `platemap_file`.

The platemap has two columns besides the well position, `cell_line` and `treatment` (`DMSO` or `drug`).
Each cell line is named for the case it tests when its drug wells are compared with its DMSO wells (reference normalization and phenotypic activity):

| `cell_line` | Wells per plate | Outcome |
| ----------- | --------------- | ------- |
| `mixed` | 3 DMSO, 5 drug | reference normalized and scored |
| `dmso_only` | 8 DMSO, plus 4 DMSO edge wells | reference normalized; phenotypic activity has no drug wells to score, so it skips the group |
| `one_dmso` | 1 DMSO, 7 drug | reference normalization skips the group (fewer than 2 reference wells per plate), so it is not scored |

The edge wells (`A01`, `A24`, `P01`, `P24`) are removed by the edge well filter; they also mark the plates as 384-well plates.
After they are removed, each cell line has 16 wells across the two plates, enough for embeddings and clustering.
A group with no reference wells at all is tested by the stub run (see `stub/README.md`).

- `samplesheet.csv`: samplesheet for both plates, used by the `test` profile
- `analysis_groups.yml`: analysis groups for the `test` profile
- `test-project/test/workspace/metadata/platemaps/<batch>/`: `barcode_platemap.csv` and the platemap
- `test-project/test/workspace/profiles/<batch>_<plate_layout>/<plate>/<plate>_converted.parquet`: single-cell profiles (generated)

## Generating the profiles

The parquet files are not tracked by git.
To create them, run the following from the repository root:

```bash
uv run scripts/generate_test_data.py
```

The script simulates cells with random feature values for every well in each plate's platemap.
Only the well positions are used, so feature values don't depend on cell line or treatment.
To change the test data, edit the samplesheet and platemaps, then rerun the script.

## Running the pipeline

The `test` profile uses these data.
From the repository root:

```bash
nextflow run . -profile test,docker --outdir results/test-data
```

The `-profile test` nf-test in `tests/default.nf.test` also runs the pipeline on these data.
