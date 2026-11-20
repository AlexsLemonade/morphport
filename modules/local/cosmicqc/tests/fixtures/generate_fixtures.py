#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "pandas==2.3.3",
#     "pyarrow==22.0.0",
# ]
# ///
"""
Generate the small fixtures for the coSMicQC filter module tests.

Writes these files to the directory containing this script.

Plate P1, a per-cell plate as PYCYTOMINER_ANNOTATE writes it, with 5 cells in each of 7 wells (35 rows):

- profiles_P1.parquet: wells A01, B02, C03, D06, G11, H06, and E12.
  The highest row is H and the highest column is 12, so the plate is inferred to be a 96-well plate.
  Its edge wells are A01 (a corner), H06 (edge row only), and E12 (edge column only);
  removing them leaves the 20 cells in the interior wells B02, C03, D06, and G11.
  Text metadata columns are pandas string[pyarrow] columns, as annotation writes them.
  Feature values follow a small repeating pattern, except:
    - cell 7 (B02) is extreme in both Nuclei_Intensity_MassDisplacement_CorrDNA and
      Nuclei_Intensity_IntegratedIntensity_CorrDNA, so it is flagged by two outlier conditions
    - cell 15 (D06) is extreme in Cytoplasm_AreaShape_Area
    - cells 18 (D06) and 24 (G11) are NaN in Cytoplasm_AreaShape_Area
    - Nuclei_AreaShape_Eccentricity has values only in cells 18 and 24, so no cell has values
      for both it and Cytoplasm_AreaShape_Area
    - Cells_Intensity_MeanIntensity_Mito is NaN in every cell
    - Cells_AreaShape_EulerNumber alternates between 1 and 0; with only two values, its mean lies
      between them, so no cell has a z-score of 0
- profiles_P1_no_image_number.parquet: the same plate without the Metadata_ImageNumber column

Outlier condition settings:

- settings_main.json: for profiles_P1.parquet, with
    - clustered and bright: each flags cell 7
    - irregular: flags no cells
    - large_cytoplasm: flags cell 15; cells 18 and 24 are NaN and not evaluated
    - missing_feature: uses Cells_AreaShape_Eccentricity, which is not in the plate, so it is skipped
    - no_mito: uses Cells_Intensity_MeanIntensity_Mito, which is NaN in every cell, so it is skipped
    - eccentric_cytoplasm: uses Cytoplasm_AreaShape_Area and Nuclei_AreaShape_Eccentricity,
      which no cell has values for both of, so it is skipped
- settings_all_skipped.json: missing_feature and no_mito only, so every condition is skipped
- settings_remove_all.json: below_mean and above_mean, with thresholds of -0.001 and 0.001 on
  Cells_AreaShape_EulerNumber, so together they flag every cell
- settings_none.json: an empty list, so no outlier conditions are applied
- settings_invalid.json: a threshold of 0, a boolean threshold, and a repeated condition name

Plate P1_empty, with the same columns as P1 and no rows:

- profiles_P1_empty.parquet

Usage (from the repository root):

    uv run modules/local/cosmicqc/tests/fixtures/generate_fixtures.py
"""

import json
import pathlib

import pandas as pd

FIXTURE_DIR = pathlib.Path(__file__).parent
# the designed cells below are in the interior wells, which come first after A01
WELLS = ["A01", "B02", "C03", "D06", "G11", "H06", "E12"]
CELLS_PER_WELL = 5

# outlier cells, by row
CLUSTERED_BRIGHT_CELL = 7
LARGE_CYTOPLASM_CELL = 15
CYTOPLASM_NAN_CELLS = [18, 24]


def pattern(n_rows: int, base: float, step: float, offset: int) -> list[float]:
    """Values that repeat over 7 levels, so no single cell stands out."""
    return [base + step * ((row * 3 + offset) % 7) for row in range(n_rows)]


def plate_p1_profiles() -> pd.DataFrame:
    """Five cells per well for plate P1, with designed outliers and NaN values."""
    n_rows = len(WELLS) * CELLS_PER_WELL
    df = pd.DataFrame(
        {
            "Metadata_cell_line": ["lineA"] * n_rows,
            "Metadata_treatment": [["DMSO", "drugA"][row % 2] for row in range(n_rows)],
            "Metadata_Plate": ["P1"] * n_rows,
            "Metadata_Well": [well for well in WELLS for _ in range(CELLS_PER_WELL)],
            "Metadata_ImageNumber": [
                image
                for image in range(1, len(WELLS) + 1)
                for _ in range(CELLS_PER_WELL)
            ],
            "Metadata_ObjectNumber": list(range(1, CELLS_PER_WELL + 1)) * len(WELLS),
            "Cells_AreaShape_EulerNumber": [float(row % 2) for row in range(n_rows)],
            "Cells_Intensity_IntegratedIntensity_CorrDNA": pattern(
                n_rows, 50.0, 1.0, 0
            ),
            "Cells_Intensity_MeanIntensity_Mito": [float("nan")] * n_rows,
            "Cytoplasm_AreaShape_Area": pattern(n_rows, 300.0, 5.0, 1),
            "Nuclei_AreaShape_Eccentricity": [float("nan")] * n_rows,
            "Nuclei_AreaShape_Solidity": pattern(n_rows, 0.90, 0.01, 2),
            "Nuclei_Intensity_IntegratedIntensity_CorrDNA": pattern(
                n_rows, 20.0, 1.0, 3
            ),
            "Nuclei_Intensity_MassDisplacement_CorrDNA": pattern(n_rows, 1.0, 0.1, 4),
        }
    )
    df.loc[CLUSTERED_BRIGHT_CELL, "Nuclei_Intensity_MassDisplacement_CorrDNA"] = 10.0
    df.loc[CLUSTERED_BRIGHT_CELL, "Nuclei_Intensity_IntegratedIntensity_CorrDNA"] = (
        200.0
    )
    df.loc[LARGE_CYTOPLASM_CELL, "Cytoplasm_AreaShape_Area"] = 3000.0
    df.loc[CYTOPLASM_NAN_CELLS, "Cytoplasm_AreaShape_Area"] = float("nan")
    df.loc[CYTOPLASM_NAN_CELLS, "Nuclei_AreaShape_Eccentricity"] = 0.5
    text_columns = [
        "Metadata_cell_line",
        "Metadata_treatment",
        "Metadata_Plate",
        "Metadata_Well",
    ]
    return df.astype({col: "string[pyarrow]" for col in text_columns})


SETTINGS_MAIN = [
    {
        "name": "clustered",
        "thresholds": {"Nuclei_Intensity_MassDisplacement_CorrDNA": 2},
    },
    {
        "name": "bright",
        "thresholds": {"Nuclei_Intensity_IntegratedIntensity_CorrDNA": 2},
    },
    {"name": "irregular", "thresholds": {"Nuclei_AreaShape_Solidity": -10}},
    {"name": "large_cytoplasm", "thresholds": {"Cytoplasm_AreaShape_Area": 2}},
    {"name": "missing_feature", "thresholds": {"Cells_AreaShape_Eccentricity": 2}},
    {"name": "no_mito", "thresholds": {"Cells_Intensity_MeanIntensity_Mito": 2}},
    {
        "name": "eccentric_cytoplasm",
        "thresholds": {
            "Cytoplasm_AreaShape_Area": 2,
            "Nuclei_AreaShape_Eccentricity": 2,
        },
    },
]

SETTINGS_ALL_SKIPPED = [
    {"name": "missing_feature", "thresholds": {"Cells_AreaShape_Eccentricity": 2}},
    {"name": "no_mito", "thresholds": {"Cells_Intensity_MeanIntensity_Mito": 2}},
]

SETTINGS_INVALID = [
    {
        "name": "clustered",
        "thresholds": {"Nuclei_Intensity_MassDisplacement_CorrDNA": 0},
    },
    {
        "name": "bright",
        "thresholds": {"Nuclei_Intensity_IntegratedIntensity_CorrDNA": True},
    },
    {"name": "clustered", "thresholds": {"Nuclei_AreaShape_Solidity": -2}},
]

SETTINGS_REMOVE_ALL = [
    {"name": "below_mean", "thresholds": {"Cells_AreaShape_EulerNumber": -0.001}},
    {"name": "above_mean", "thresholds": {"Cells_AreaShape_EulerNumber": 0.001}},
]


def write_json(data, name: str) -> None:
    with open(FIXTURE_DIR / name, "w") as f:
        json.dump(data, f, indent=4)
        f.write("\n")


def main() -> None:
    p1 = plate_p1_profiles()
    p1.to_parquet(FIXTURE_DIR / "profiles_P1.parquet", index=False)
    p1.drop(columns="Metadata_ImageNumber").to_parquet(
        FIXTURE_DIR / "profiles_P1_no_image_number.parquet", index=False
    )
    p1.head(0).to_parquet(FIXTURE_DIR / "profiles_P1_empty.parquet", index=False)
    write_json(SETTINGS_MAIN, "settings_main.json")
    write_json(SETTINGS_ALL_SKIPPED, "settings_all_skipped.json")
    write_json(SETTINGS_REMOVE_ALL, "settings_remove_all.json")
    write_json([], "settings_none.json")
    write_json(SETTINGS_INVALID, "settings_invalid.json")


if __name__ == "__main__":
    main()
