# Simulated full-size test data

This directory holds simulated Cell Painting datasets based on the Pediatric Cancer Cell Morphology Atlas (PCCMA) metadata in `assets/pccma`, for the `test_full` profiles.
The plates and their layouts are the real PCCMA ones; the single-cell profiles are simulated by `scripts/generate_full_test_data.py`.
Unlike the small synthetic data in `tests/data`, these data have a more complete feature set and structured effects. They are still smaller than a full run (fewer cells and features), but should allow exercising most modes of filtering and analysis in the workflow.
They will be replaced by real data from the Cell Painting Gallery once it is available.

The data use the Cell Painting Gallery directory structure and start at the converted parquet stage, so plates use `cytotable_preset: skip`.
Project IDs are placeholders until the gallery IDs are known, and the source is `simulated`.

## Datasets

| Dataset | Project                 | Batch   | Plates                                   |
| ------- | ----------------------- | ------- | ---------------------------------------- |
| `atlas` | `pccma-atlas-simulated` | `Atlas` | `BR00150695`, `BR00150696`, `BR00150697` |

### Atlas

The atlas plates hold 14 cell lines, each seeded in 4 interior wells of each plate.
Each plate is one time point: 24, 48, or 72 hours after seeding.
The platemap columns include `Cell_Line_Name` and `time_point`.

## Simulated profiles

Each plate has about 100 cells per well, with about 600 features in CellProfiler style, covering the `Nuclei`, `Cells`, and `Cytoplasm` compartments, the main measurement types (`AreaShape`, `Intensity`, `Texture`, `Granularity`, `RadialDistribution`, `Correlation`, and `Neighbors`, the last only for `Nuclei` and `Cells`), and all five imaging channels.
Channel names (`AGP`, `DNA`, `ER`, `Mito`, and `RNA`) follow the PCCMA CellProfiler pipelines, not the JUMP names (e.g. `CorrDNA`) used by the default coSMicQC filter settings; use `assets/cosmicqc_filter_settings_pccma.json` for these data.
Features measured in the same channel are correlated, as in real data.

Profiles are simulated with these effects:

- Cell lines: each cell line has its own profile offset, the same in every dataset.
- Time point (atlas): an effect growing from 24 to 72 hours, faster for some cell lines than others; cell counts per well also grow with time.
- Noise: plate offsets, well-to-well variation, and cell-to-cell variation.

The effects are randomly drawn, but with a fixed seed, so the data are the same each time they are generated.

## Files

- `<project>/simulated/workspace/metadata/platemaps/<batch>/`: `barcode_platemap.csv` and the platemaps, copied from `assets/pccma/platemaps` (generated)
- `<project>/simulated/workspace/profiles/<batch>/<plate>/<plate>_converted.parquet`: single-cell profiles (generated)

## Generating the data

The generated files are not tracked by git.
The script reads the PCCMA platemaps from `assets/pccma/platemaps`, which are also not tracked; build them first as described in `assets/pccma/README.md`.
Then run the following from the repository root:

```bash
uv run scripts/generate_full_test_data.py
```

To generate only some datasets, name them with `--dataset`, e.g. `--dataset atlas`.
