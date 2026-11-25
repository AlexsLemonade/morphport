# Simulated full-size test data

This directory holds simulated Cell Painting datasets based on the Pediatric Cancer Cell Morphology Atlas (PCCMA) metadata in `assets/pccma`, for the `test_full` and `test_full_screen` profiles.
The plates and their layouts are the real PCCMA ones; the single-cell profiles are simulated by `scripts/generate_full_test_data.py`.
Unlike the small synthetic data in `tests/data`, these data have a more complete feature set and structured effects. They are still smaller than a full run (fewer cells and features), but should allow exercising most modes of filtering and analysis in the workflow.
They will be replaced by real data from the Cell Painting Gallery once it is available.

The data use the Cell Painting Gallery directory structure and start at the converted parquet stage, so plates use `cytotable_preset: skip`.
Project IDs are placeholders until the gallery IDs are known, and the source is `simulated`.

## Datasets

| Dataset  | Project                 | Batch                  | Plates                                                                   |
| -------- | ----------------------- | ---------------------- | ------------------------------------------------------------------------ |
| `atlas`  | `pccma-atlas-simulated` | `Atlas`                | `BR00150695`, `BR00150696`, `BR00150697`                                 |
| `screen` | `pccma-repo1-simulated` | `SK-N-AS_REPO1_Screen` | `BR00148919`–`BR00148924` (SK-N-AS), `SIM00148919`–`SIM00148924` (U2-OS) |

### Atlas

The atlas plates hold 14 cell lines, each seeded in 4 interior wells of each plate.
Each plate is one time point: 24, 48, or 72 hours after seeding.
The platemap columns include `Cell_Line_Name` and `time_point`.

### Screen

The screen plates hold SK-N-AS cells treated with compounds from the REPO1 library.
They use two plate layouts, each with three replicate plates: `BR00148919`–`BR00148921` and `BR00148922`–`BR00148924`.
The real screen has only SK-N-AS, but to test screens with more than one cell line, the simulated data add U2-OS plates with the same layouts.
Their barcodes are made up, `SIM` and the number of the plate whose layout they copy (e.g. `SIM00148919` copies `BR00148919`), and their platemaps are copies with `Cell_Line_Name` set to `U2-OS`.
Each plate has 320 wells of test compounds, 32 wells of DMSO (the negative control), and 32 untreated wells, in the first two and last two columns.
The platemap columns include `Treatment_Primary_Treatment` (the compound name, `DMSO`, or `untreated`), `Treatment_Control_Class` (`Treatment`, `NegCon`, or `Control`), `Treatment_Mechanism` (the mechanism of action, with multiple mechanisms separated by `|`), and `compound_id`.
The full screen has more layouts, and two Row O repeat plates that re-test one row of compounds from each original layout; these are not included.

## Simulated profiles

Each plate has about 50 cells per well, with about 600 features in CellProfiler style, covering the `Nuclei`, `Cells`, and `Cytoplasm` compartments, the main measurement types (`AreaShape`, `Intensity`, `Texture`, `Granularity`, `RadialDistribution`, `Correlation`, and `Neighbors`, the last only for `Nuclei` and `Cells`), and all five imaging channels.
Channel names (`AGP`, `DNA`, `ER`, `Mito`, and `RNA`) follow the PCCMA CellProfiler pipelines, not the JUMP names (e.g. `CorrDNA`) used by the default coSMicQC filter settings; use `assets/cosmicqc_filter_settings_pccma.json` for these data.
Features measured in the same channel are correlated, as in real data.
Features that measure nearly the same thing (e.g. a cell's area and perimeter, or a channel's mean and median intensity) are highly correlated even between single cells, so feature selection removes some of them, as it would in real data.

Profiles are simulated with these effects:

- Cell lines: each cell line has its own profile offset, the same in every dataset.
- Time point (atlas): an effect growing from 24 to 72 hours, faster for some cell lines than others; cell counts per well also grow with time.
- Compounds (screen): about 10% of test compounds are active in each cell line, with effects of varying size; the others have no effect.
  About half of the compounds active in one cell line are active in both; the rest are active in only one.
  A compound's effect has the same direction in both cell lines, but its size varies between them.
  Active compounds with the same mechanism of action share part of their effect direction.
  Untreated wells are slightly offset from DMSO wells.
  Cell counts do not vary with treatment.
- Noise: plate offsets, well-to-well variation, and cell-to-cell variation.

The effects are randomly drawn, but with a fixed seed, so the data are the same each time they are generated.

## Files

- `atlas_samplesheet.csv`: samplesheet for the atlas plates, used by the `test_full` profile
- `atlas_analysis_groups.yml`: analysis groups for the atlas data: at the cell level, cells grouped by time point; at the well level, each cell line compared with `U2-OS` at each time point
- `screen_samplesheet.csv`: samplesheet for the screen plates, used by the `test_full_screen` profile
- `screen_analysis_groups.yml`: analysis groups for the screen data: all wells of each cell line, at both aggregate levels, with every compound and the untreated wells compared with DMSO at the well level
- `<project>/simulated/workspace/metadata/platemaps/<batch>/`: `barcode_platemap.csv` and the platemaps, copied from `assets/pccma/platemaps`, with the simulated U2-OS screen plates added (generated)
- `<project>/simulated/workspace/profiles/<batch>/<plate>/<plate>_converted.parquet`: single-cell profiles (generated)

## Generating the data

The generated files are not tracked by git.
The script reads the PCCMA platemaps from `assets/pccma/platemaps`, which are also not tracked; build them first as described in `assets/pccma/README.md`.
Then run the following from the repository root:

```bash
uv run scripts/generate_full_test_data.py
```

To generate only some datasets, name them with `--dataset`, e.g. `--dataset atlas`.

## Running the pipeline

After generating the data, run the `test_full` profile (atlas) or the `test_full_screen` profile (screen) from the repository root, as the samplesheet paths are relative to it:

```bash
nextflow run . -profile test_full,docker --outdir <OUTDIR>
nextflow run . -profile test_full_screen,docker --outdir <OUTDIR>
```
