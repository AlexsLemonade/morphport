#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "polars==1.35.2",
# ]
# ///
"""
Generate the small fixtures for the pycytominer annotate module tests.

Writes these files to the directory containing this script.

Plate P1, split across two parquet files as CytoTable can write a plate in parts,
with 2 cells in each of wells A01 to A04 (8 rows together):

- profiles_P1_part1.parquet: wells A01 and A02
- profiles_P1_part2.parquet: wells A03 and A04

Each has the CytoTable metadata columns (Metadata_Plate, Metadata_Well, Metadata_ImageNumber,
Metadata_ObjectNumber) and two features.

Platemaps for P1, with the same layout in two formats:

- platemap_P1.csv: comma-delimited with a UTF-8 byte order mark and the well column well_position.
  It covers the text handling of platemap values:
  surrounding whitespace in a header (" treatment ") and a value (" drugA ") is stripped;
  dose '010' stays text, not the number 10;
  in the note column, a blank value and a row with the trailing field missing become '_NA_',
  while the NA-like strings 'NA' and 'null' are kept as text.
  It also lists well A05, which has no cells, so it adds no rows.
- platemap_P1.txt: tab-delimited, with the well column Well (the same name as in the profiles),
  and a plate_map_name column first, as in Cell Painting Gallery platemaps

Plate P2, for resolving duplicated metadata columns, with 1 cell in each of wells A01 to A04:

- profiles_P2.parquet: has profile metadata columns that collide with platemap columns:
    - Metadata_dose: integers equal to the platemap's text values, so identical as text
    - Metadata_treatment: missing in A02 but otherwise equal to the platemap, so coalesced
    - Metadata_cell_line: lineC in A04 where the platemap has lineB, so conflicting
  and columns duplicated within the profiles, as CytoTable joins can produce:
    - Metadata_Site, Metadata_Site_1 (DuckDB numeric suffix), and Image_Metadata_Site
      (compartment prefix), all identical
    - Metadata_FOV_1: a numeric suffix without a Metadata_FOV column, so not a duplicate
- platemap_P2.csv: well_position, cell_line, treatment, dose, and perturbation, which has the same
  values as treatment under an unrelated name, so it is reported but not dropped

Plate P3, with no Metadata_Well column (only Image_Metadata_Well), 1 cell in each of wells A01 to A04:

- profiles_P3_no_well.parquet

Plates for unmatched wells (wells with profiles but no platemap row), both used with platemap_P1.csv:

- profiles_P4_unmatched.parquet: 2 cells in each of wells A01 and A02 (in the platemap),
  3 cells in well A06 (not in the platemap), and 1 cell with no well value
- profiles_P5_no_match.parquet: 1 cell in each of wells A1 to A4, without the zero padding the platemap uses,
  so no well matches

Usage (from the repository root):

    uv run modules/local/pycytominer/annotate/tests/fixtures/generate_fixtures.py
"""

import pathlib

import polars as pl

FIXTURE_DIR = pathlib.Path(__file__).parent
WELLS = ["A01", "A02", "A03", "A04"]


def plate_p1_profiles() -> pl.DataFrame:
    """Two cells per well for plate P1."""
    cells_per_well = 2
    n_rows = len(WELLS) * cells_per_well
    return pl.DataFrame(
        {
            "Metadata_Plate": ["P1"] * n_rows,
            "Metadata_Well": [well for well in WELLS for _ in range(cells_per_well)],
            "Metadata_ImageNumber": [
                image
                for image in range(1, len(WELLS) + 1)
                for _ in range(cells_per_well)
            ],
            "Metadata_ObjectNumber": list(range(1, cells_per_well + 1)) * len(WELLS),
            "Cells_AreaShape_Area": [float(100 + row) for row in range(n_rows)],
            "Nuclei_Intensity_MeanIntensity_DNA": [row / 10 for row in range(n_rows)],
        }
    )


def plate_p2_profiles() -> pl.DataFrame:
    """One cell per well for plate P2, with duplicated metadata columns."""
    return pl.DataFrame(
        {
            "Metadata_Plate": ["P2"] * len(WELLS),
            "Metadata_Well": WELLS,
            "Metadata_dose": [10, 10, 1, 1],
            "Metadata_treatment": ["DMSO", None, "DMSO", "drugA"],
            "Metadata_cell_line": ["lineA", "lineA", "lineB", "lineC"],
            "Metadata_Site": [1, 2, 1, 2],
            "Metadata_Site_1": [1, 2, 1, 2],
            "Metadata_FOV_1": [3, 4, 5, 6],
            "Image_Metadata_Site": [1, 2, 1, 2],
            "Cells_AreaShape_Area": [100.0, 101.0, 102.0, 103.0],
        }
    )


def plate_p3_profiles() -> pl.DataFrame:
    """One cell per well for plate P3, with the well only in Image_Metadata_Well."""
    return pl.DataFrame(
        {
            "Metadata_Plate": ["P3"] * len(WELLS),
            "Image_Metadata_Well": WELLS,
            "Cells_AreaShape_Area": [100.0, 101.0, 102.0, 103.0],
        }
    )


def plate_p4_profiles() -> pl.DataFrame:
    """Plate P4: wells A01 and A02 match platemap_P1.csv; A06 and a missing well do not."""
    wells = ["A01", "A01", "A02", "A02", "A06", "A06", "A06", None]
    return pl.DataFrame(
        {
            "Metadata_Plate": ["P4"] * len(wells),
            "Metadata_Well": wells,
            "Cells_AreaShape_Area": [float(100 + row) for row in range(len(wells))],
        }
    )


def plate_p5_profiles() -> pl.DataFrame:
    """Plate P5: wells without zero padding, so none match platemap_P1.csv."""
    wells = ["A1", "A2", "A3", "A4"]
    return pl.DataFrame(
        {
            "Metadata_Plate": ["P5"] * len(wells),
            "Metadata_Well": wells,
            "Cells_AreaShape_Area": [100.0, 101.0, 102.0, 103.0],
        }
    )


# Platemaps are written as literal text, to control whitespace, blank values, and missing fields.
PLATEMAP_P1_CSV = (
    # byte order mark, as written by Excel
    "﻿"
    "well_position,cell_line, treatment ,dose,note\n"
    "A01,lineA,DMSO,010,\n"
    "A02,lineA, drugA ,1,NA\n"
    # the trailing note field is missing
    "A03,lineB,DMSO,010\n"
    "A04,lineB,drugA,1,null\n"
    # no cells in A05
    "A05,lineB,drugA,1,extra\n"
)

PLATEMAP_P1_TXT = (
    "plate_map_name\tWell\tcell_line\ttreatment\tdose\tnote\n"
    "layout_1\tA01\tlineA\tDMSO\t010\t\n"
    "layout_1\tA02\tlineA\tdrugA\t1\tNA\n"
    "layout_1\tA03\tlineB\tDMSO\t010\t\n"
    "layout_1\tA04\tlineB\tdrugA\t1\tnull\n"
)

PLATEMAP_P2_CSV = (
    "well_position,cell_line,treatment,dose,perturbation\n"
    "A01,lineA,DMSO,10,DMSO\n"
    "A02,lineA,drugA,10,drugA\n"
    "A03,lineB,DMSO,1,DMSO\n"
    "A04,lineB,drugA,1,drugA\n"
)


def main():
    p1 = plate_p1_profiles()
    p1.filter(pl.col("Metadata_Well").is_in(["A01", "A02"])).write_parquet(
        FIXTURE_DIR / "profiles_P1_part1.parquet"
    )
    p1.filter(pl.col("Metadata_Well").is_in(["A03", "A04"])).write_parquet(
        FIXTURE_DIR / "profiles_P1_part2.parquet"
    )
    plate_p2_profiles().write_parquet(FIXTURE_DIR / "profiles_P2.parquet")
    plate_p3_profiles().write_parquet(FIXTURE_DIR / "profiles_P3_no_well.parquet")
    plate_p4_profiles().write_parquet(FIXTURE_DIR / "profiles_P4_unmatched.parquet")
    plate_p5_profiles().write_parquet(FIXTURE_DIR / "profiles_P5_no_match.parquet")

    (FIXTURE_DIR / "platemap_P1.csv").write_text(PLATEMAP_P1_CSV, encoding="utf-8")
    (FIXTURE_DIR / "platemap_P1.txt").write_text(PLATEMAP_P1_TXT, encoding="utf-8")
    (FIXTURE_DIR / "platemap_P2.csv").write_text(PLATEMAP_P2_CSV, encoding="utf-8")


if __name__ == "__main__":
    main()
