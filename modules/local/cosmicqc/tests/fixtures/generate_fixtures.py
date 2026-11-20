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

Plate P1, a per-cell plate as PYCYTOMINER_ANNOTATE writes it, with 5 cells in each of 6 wells (30 rows):

- profiles_P1.parquet: wells A01, B02, C03, D06, G11, and H12.
  H12 is the highest row and column, so the plate is inferred to be a 96-well plate,
  and A01 and H12 are its edge wells; removing them leaves 20 cells.
  Text metadata columns are pandas string[pyarrow] columns, as annotation writes them.
  Feature values follow a small repeating pattern, except:
    - cell 7 (B02) is extreme in both Nuclei_Intensity_MassDisplacement_CorrDNA and
      Nuclei_Intensity_IntegratedIntensity_CorrDNA, so it is flagged by two outlier conditions
    - cell 15 (D06) is extreme in Cytoplasm_AreaShape_Area
    - cells 18 (D06) and 24 (G11) are NaN in Cytoplasm_AreaShape_Area
    - Cells_Intensity_MeanIntensity_Mito is NaN in every cell
- profiles_P1_no_image_number.parquet: the same plate without the Metadata_ImageNumber column

Outlier condition settings:

- settings_main.json: for profiles_P1.parquet, with
    - clustered and bright: each flags cell 7
    - irregular: flags no cells
    - large_cytoplasm: flags cell 15; cells 18 and 24 are NaN and not evaluated
    - missing_feature: uses Cells_AreaShape_Eccentricity, which is not in the plate, so it is skipped

Usage (from the repository root):

    uv run modules/local/cosmicqc/tests/fixtures/generate_fixtures.py
"""

import json
import pathlib

import pandas as pd

FIXTURE_DIR = pathlib.Path(__file__).parent
WELLS = ["A01", "B02", "C03", "D06", "G11", "H12"]
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
            "Metadata_treatment": ["DMSO", "drugA"] * (n_rows // 2),
            "Metadata_Plate": ["P1"] * n_rows,
            "Metadata_Well": [well for well in WELLS for _ in range(CELLS_PER_WELL)],
            "Metadata_ImageNumber": [
                image
                for image in range(1, len(WELLS) + 1)
                for _ in range(CELLS_PER_WELL)
            ],
            "Metadata_ObjectNumber": list(range(1, CELLS_PER_WELL + 1)) * len(WELLS),
            "Cells_Intensity_IntegratedIntensity_CorrDNA": pattern(
                n_rows, 50.0, 1.0, 0
            ),
            "Cells_Intensity_MeanIntensity_Mito": [float("nan")] * n_rows,
            "Cytoplasm_AreaShape_Area": pattern(n_rows, 300.0, 5.0, 1),
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
]


def write_json(data, name: str) -> None:
    with open(FIXTURE_DIR / name, "w") as f:
        json.dump(data, f, indent=2)
        f.write("\n")


def main() -> None:
    p1 = plate_p1_profiles()
    p1.to_parquet(FIXTURE_DIR / "profiles_P1.parquet", index=False)
    p1.drop(columns="Metadata_ImageNumber").to_parquet(
        FIXTURE_DIR / "profiles_P1_no_image_number.parquet", index=False
    )
    write_json(SETTINGS_MAIN, "settings_main.json")


if __name__ == "__main__":
    main()
