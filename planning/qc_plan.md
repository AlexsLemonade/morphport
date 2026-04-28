# QC Step Planning

## Reference Notebook

We are working from the Way Lab QC notebook at [`3.preprocessing_features/1.sc_quality_control.ipynb`](https://github.com/WayScience/pediatric_cancer_atlas_profiling/blob/main/3.preprocessing_features/1.sc_quality_control.ipynb) from the `pediatric_cancer_atlas_profiling` repository.
That notebook processes per-plate parquet files (converted from SQLite by CytoTable) using [coSMicQC](https://github.com/WayScience/coSMicQC).

### QC Criteria Applied in Reference

All filtering is done at the **single-cell level** using `coSMicQC.find_outliers()`, which computes z-scores for specified features and flags cells exceeding given thresholds.
Three distinct failure conditions are evaluated independently; any cell failing one or more is dropped.
The main notebook does QC and includes visual reports for representative cells, but we are not planning to do that in the initial filtering here (though maybe as a separate report later).

#### 1. Clustered nuclei

Identifies overlapping nuclei that were misidentified as a single object.

| Feature                                        | Threshold (z-score) | Remove |
| ---------------------------------------------- | ------------------- | ------ |
| `Nuclei_Intensity_MassDisplacement_CorrDNA`    | 0.05                | above  |
| `Nuclei_Intensity_IntegratedIntensity_CorrDNA` | 1.5                 | above  |

Both features must exceed their thresholds simultaneously (the `find_outliers` call takes a dict, and the function flags cells where **all** features in the dict are outliers together).
In this case (based on comments), the first feature is set deliberately low to catch as many potential multi-nuclei cases as possible, while the second feature (total DNA intensity) is set higher to avoid flagging normal cells with slight mass displacement.

### 2. Irregular nuclei (mis-segmented)

Identifies nuclei with irregular shapes (harsh outlines, protrusions).
This parameter may not apply to the full PCCMA dataset, since it is being tuned based on untreated cells.
(This is part of the reason we are keeping the thresholds flexible.)

| Feature                     | Threshold (z-score) | Remove |
| --------------------------- | ------------------- | ------ |
| `Nuclei_AreaShape_Solidity` | -1.6                | below  |

#### 3. Multi-nuclei cells

Identifies whole-cell segmentations containing multiple undetected nuclei (high total DNA intensity despite a single-cell segmentation).
This happens when nuclei are discarded before segmentation, so the cells are segmented as a single object but in reality were based on multiple cells with multiple nuclei.

| Feature                                       | Threshold (z-score) | Remove |
| --------------------------------------------- | ------------------- | ------ |
| `Cells_Intensity_IntegratedIntensity_CorrDNA` | 0.5                 | above  |

---

## Pipeline plans:

### Inputs:

- from the pipeline channel: `(metadata, parquet_file)` (output of `cytotable_convert`)
- filter settings: A JSON file with the feature names and thresholds to apply, which can be generated from the reference notebook and tuned as needed.
  This allows the filtering logic to be decoupled from the specific thresholds, which may need to be adjusted for different datasets or cell types.

#### Filter settings JSON format

Each entry in the list represents one `find_outliers()` call, where all features within an entry are ANDed together (all must be outliers for a cell to be flagged).
Entries are ORed — a cell failing any entry is removed.
The sign of each threshold value encodes direction: positive values flag cells with z-scores **above** the threshold; negative values flag cells with z-scores **below** the threshold.
This matches the coSMicQC convention directly, so threshold values can be passed to `find_outliers()` without transformation.

```json
[
  {
    "name": "clustered_nuclei",
    "thresholds": {
      "Nuclei_Intensity_MassDisplacement_CorrDNA": 0.05,
      "Nuclei_Intensity_IntegratedIntensity_CorrDNA": 1.5
    }
  },
  {
    "name": "irregular_nuclei",
    "thresholds": {
      "Nuclei_AreaShape_Solidity": -1.6
    }
  },
  ...
]
```

### Outputs:

- `(updated_metadata, filtered_parquet_file)` — cleaned per-cell parquet, bad cells removed; metadata updated with summary QC stats (see below)
- `qc_summary.json` — full per-plate QC record combining the filter settings used and the results (cell counts before/after, per-condition failure counts); suitable for downstream aggregation and reporting
- Outlier index (`outlier_indices.csv.gz`) — identifies which cells were removed and why, with a column indicating which condition(s) flagged each cell; compressed CSV for human-readability and easy inspection

The thresholds in the reference notebook were tuned for pilot data, and we want to make sure that we can adjust them easily as needed.

#### QC summary and metadata

The `qc_summary.json` output embeds both the filter settings that were applied and the per-plate results in a single file, making it straightforward to reconstruct exactly what was run and compare results across threshold configurations.
Key QC stats should also be added to the metadata map flowing forward through the pipeline.
This lets downstream workflow logic branch or warn on QC outcomes (e.g., flagging plates that lost an unusually high fraction of cells) without needing to read a file.
The metadata fields to add are deliberately minimal — full detail lives in `qc_summary.json`:

- `qc_cells_input` — total cells before filtering
- `qc_cells_output` — cells remaining after filtering
- `qc_fraction_cells_removed` — fraction removed (for easy threshold checks in workflow logic)

### QC Report

Report generation (the `CytoDataFrame`-based visual inspection from the original reference notebook) can be implemented as a **separate, optional process** to avoid complicating the core QC filtering logic and to allow it to be toggled on/off as needed.
This saves us from having to worry about the images and compartment outlines in the core workflow, at least for now.

## Planned Processes

### Core QC process (`COSMICQC_FILTER`)

- **Input:**
  - `(metadata, parquet_file)`
  - filter settings JSON (see format above)
- **Outputs:**
  - `(updated_metadata, filtered_parquet_file)` — metadata updated with `qc_cells_input`, `qc_cells_output`, `qc_fraction_removed`
  - `qc_summary.json` — filter settings + per-condition results combined
  - `outlier_indices.csv.gz` — one row per removed cell, with a column for the condition(s) that flagged it

### Report process (`COSMICQC_REPORT`) _(optional)_

- **Input:** `(metadata, parquet_file, outlier_indices_file, image_dir)`
  - Takes the **pre-filter** parquet so flagged cells can be rendered.
  - `image_dir` points to the original TIFF files on S3 (or local path if available) for rendering cell crops.
  - `image_dir` is passed as a string (not a file/path object) since we will not necessarily know what needs to be staged by Nextflow; the process fetches the necessary files from CPG on S3 as needed.
- **Image and outline requirements:** The report script must fetch both the original images and the segmentation outline files for each compartment referenced in the filter settings JSON.
  The set of required compartments is derived by parsing the leading token of each feature name (e.g., `Nuclei_…` → Nuclei, `Cells_…` → Cells).
  For each compartment, `CytoDataFrame` needs the compartment-specific location and bounding box metadata columns to correctly position thumbnail crops.
  The script builds these column lists automatically from the compartment names rather than requiring them to be specified in the JSON.
- **Outputs:**
  - HTML or notebook report with embedded cell thumbnails for each failure category
  - Per-condition QC metric distribution plots
- **Triggered only when** `params.cosmicqc_report = true` and image files are available.

---

## Open Questions

- Will the entire dataset use the same thresholds, or will this need to be sample- or plate-specific?
  For now, we will use a single JSON file set in params, but we can revisit this if different thresholds are needed for different conditions (e.g., cell types, treatments, etc.).

## Well-Level QC

Well-level QC has not been included at this stage (no edge-well exclusion, no cell-count-per-well threshold).
However, the pipeline outline (`outline.md`) anticipates that QC may include cell count thresholds or well-level anomaly detection.
This is a natural extension to plan for, and coSMicQC supports well-level outlier detection using the same `find_outliers()` interface applied to per-well aggregated metrics.

Well-level QC steps to consider (not in reference, but expected):

- **Low cell count** — wells with fewer cells than a threshold (e.g., fewer than N cells passing per-cell QC) may indicate failed imaging or segmentation and should be flagged.
- **Edge wells** — wells on the plate border are optionally excluded from downstream analyses (the pipeline outline tracks this as an `include_edge_wells` flag carried through grouping metadata).

For now, we just want to note that this will happen later.
