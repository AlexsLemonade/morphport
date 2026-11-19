#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "polars==1.35.2",
# ]
# ///
"""
Generate the small fixtures for the partition_profiles module tests.

Writes these files to the directory containing this script:

- profiles.parquet: per-cell profiles of one plate (P1), as written by COSMICQC_FILTER,
  with 10 cells in 5 wells:

  | Metadata_Well | Metadata_cell_line | Metadata_treatment | cells |
  | ------------- | ------------------ | ------------------ | ----- |
  | A01, A02      | lineA              | DMSO               | 2 each |
  | A03           | lineA              | drug A             | 3     |
  | B01           | lineB              | drug/B             | 2     |
  | B02           | lineB              | 50%                | 1     |

  The treatment values include a space, a slash, and a percent sign, which are
  percent-encoded in Hive partition directory names.
  Features: Cells_AreaShape_Area and Nuclei_Intensity_MeanIntensity_DNA (cell index values).

- profiles_empty.parquet: the same columns with no rows, as written when coSMicQC removes every cell of a plate.

- platemap.txt: a tab-separated platemap for the same wells, for the stub run
  (plate_map_name, well_position, cell_line, treatment), with simple treatment values (DMSO and drug)
  since the stub does not percent-encode directory names.

Usage (from the repository root):

    uv run modules/local/partition_profiles/tests/fixtures/generate_fixtures.py
"""

import pathlib

import polars as pl

FIXTURE_DIR = pathlib.Path(__file__).parent

# well, cell line, treatment, number of cells
WELLS = [
    ("A01", "lineA", "DMSO", 2),
    ("A02", "lineA", "DMSO", 2),
    ("A03", "lineA", "drug A", 3),
    ("B01", "lineB", "drug/B", 2),
    ("B02", "lineB", "50%", 1),
]

STUB_TREATMENTS = {"DMSO": "DMSO", "drug A": "drug", "drug/B": "drug", "50%": "drug"}


def main():
    rows = [
        {
            "Metadata_Plate": "P1",
            "Metadata_Well": well,
            "Metadata_cell_line": cell_line,
            "Metadata_treatment": treatment,
        }
        for well, cell_line, treatment, n_cells in WELLS
        for _ in range(n_cells)
    ]
    profiles = pl.DataFrame(rows).with_columns(
        Cells_AreaShape_Area=pl.int_range(pl.len()).cast(pl.Float64),
        Nuclei_Intensity_MeanIntensity_DNA=pl.int_range(pl.len()).cast(pl.Float64) / 10,
    )
    profiles.write_parquet(FIXTURE_DIR / "profiles.parquet")
    profiles.clear().write_parquet(FIXTURE_DIR / "profiles_empty.parquet")

    platemap = pl.DataFrame(
        [
            {
                "plate_map_name": "platemap_P1",
                "well_position": well,
                "cell_line": cell_line,
                "treatment": STUB_TREATMENTS[treatment],
            }
            for well, cell_line, treatment, _n_cells in WELLS
        ]
    )
    platemap.write_csv(FIXTURE_DIR / "platemap.txt", separator="\t")


if __name__ == "__main__":
    main()
