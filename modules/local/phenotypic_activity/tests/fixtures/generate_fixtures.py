#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "polars==1.35.2",
# ]
# ///
"""
Generate the small parquet fixtures for the phenotypic_activity module tests.

The fixtures stand in for reference-normalized per-well profiles (the output of the
pycytominer reference_normalize module). They have no random noise: profiles are built from
fixed, orthogonal directions, so the rankings copairs computes, and so which comparison values are active,
do not depend on any seed. This is unrealistic, but makes the tests deterministic.

Each profile is the sum of:

- a cell type baseline: Cells_Baseline_A (lineA) or Cells_Baseline_B (lineB) set to BASELINE (4);
- for treated wells of an active comparison value, a treatment effect: Cells_Shift set to SHIFT (4);
- a well-specific offset: a Nuclei_Unique_NN column of the well's own, so no two wells are
  identical. Reference (DMSO) wells get offsets 0.40, 0.42, 0.44, ... and treated wells get
  offsets 1.00, 1.02, 1.04, ..., numbered separately through each fixture. The offsets differ
  between wells so that pairs of wells are not tied in similarity (ties among the reference
  wells make copairs' permutation null much less sensitive), and every reference offset is
  smaller than every treated well offset.

With cosine similarity, this fixes the rankings. For a well with offset d, a reference well
with offset r, and another treated well with offset e (r < d and r < e):

- An inactive comparison value's wells (baseline plus offset) are more similar to each of their reference
  wells (cosine 16 / sqrt((16 + d^2)(16 + r^2)), about 0.96) than to each other
  (16 / sqrt((16 + d^2)(16 + e^2)), about 0.94). Every positive ranks after every negative, so
  its mAP is as low as it can be and its p-value is 1, whatever the null draws.
- An active comparison value's wells (baseline, shift, and offset) are more similar to each other (about
  0.97) than to their reference wells (about 0.69). Every positive ranks first, so its
  mAP is 1 and its p-value is near the smallest possible (well below 0.05).
- Wells of different cell types are orthogonal apart from their offsets (cosine 0), so the
  tests can tell whether a well's negatives are matched on cell type.

Writes these files to the directory containing this script:

- profiles_treatments.parquet: one cell type (lineA) on two plates (P1, P2), for comparing
  treatments within a cell type (comparison column Metadata_treatment, reference DMSO).
  Each plate has 8 DMSO, 4 drugActive (active), and 4 drugInactive (not active) wells; P1 also
  has one drugSingle well (one well in the group, so it is dropped).
  Cytoplasm_Intensity_MeanIntensity_Mito is 0 except for one missing value in a drugActive
  well, so that feature is dropped.
- profiles_pooled_cell_types.parquet: two cell types on one plate (P1), for comparing treatments
  within a group that pools both cell types (comparison column Metadata_treatment, reference DMSO).
  lineA has 8 DMSO and 6 drugA (not active) wells; lineB has 8 DMSO and 6 drugB (active) wells.
  drugA's negatives should be lineA's 8 DMSO wells only, not all 16 DMSO wells.
  drugC has 3 wells with the treatment effect in each cell type (active), so it is scored
  against both cell types' DMSO wells, its wells matched to others of their own cell type.
  lineA also has one drugB well, which has no other drugB wells in its cell type, so it is
  dropped while drugB's lineB wells are scored.
- profiles_cell_types.parquet: two cell types on one plate (P1), for comparing cell types within
  a treatment (comparison column Metadata_cell_type, reference DMSO): each cell type has 8 DMSO
  wells; lineA has 6 drugA wells with the treatment effect (active), and lineB has 6 drugA wells
  without it (not active).
- features.txt: the features (all features except Cells_AreaShape_Area, which tests that
  unlisted features are left out).

Usage (from the repository root):

    uv run modules/local/phenotypic_activity/tests/fixtures/generate_fixtures.py
"""

import pathlib

import polars as pl

FIXTURE_DIR = pathlib.Path(__file__).parent
BASELINE_COLUMNS = {"lineA": "Cells_Baseline_A", "lineB": "Cells_Baseline_B"}
SHIFT_COLUMN = "Cells_Shift"
# The feature with a missing value in profiles_treatments.parquet
NAN_FEATURE = "Cytoplasm_Intensity_MeanIntensity_Mito"
# Enough well-specific offset columns for the largest fixture (35 wells)
N_OFFSET_COLUMNS = 40
OFFSET_COLUMNS = [f"Nuclei_Unique_{i:02d}" for i in range(N_OFFSET_COLUMNS)]
FEATURES = [*BASELINE_COLUMNS.values(), SHIFT_COLUMN, NAN_FEATURE, *OFFSET_COLUMNS]
# Not listed in features.txt, so it should be left out of scoring
UNLISTED_FEATURE = "Cells_AreaShape_Area"

BASELINE = 4.0
SHIFT = 4.0
# Well-specific offsets: the first reference well's and the first treated well's, and the step
# between consecutive wells. Reference wells' offsets stay below treated wells' (at most
# 0.40 + 15 * 0.02 = 0.70 for the 16 reference wells of the largest fixture), so an inactive
# comparison value's wells are closer to the reference wells than to each other.
FIRST_REFERENCE_OFFSET = 0.40
FIRST_TREATED_OFFSET = 1.00
OFFSET_STEP = 0.02
REFERENCE_TREATMENT = "DMSO"


def build_profiles(layout: list[tuple]) -> pl.DataFrame:
    """
    Build per-well profiles from a layout of (plate, cell_type, treatment, n_wells, shifted) tuples.

    Each well gets its own offset column, in order through the layout, with an offset that grows
    from FIRST_REFERENCE_OFFSET (reference wells) or FIRST_TREATED_OFFSET (treated wells) by
    OFFSET_STEP per well; shifted adds the treatment effect. Wells are named in row A for lineA and row B for lineB, numbered from 1 on each plate
    and cell type.
    """
    rows = []
    next_well = {}
    n_reference_wells = 0
    n_treated_wells = 0
    for plate, cell_type, treatment, n_wells, shifted in layout:
        for _ in range(n_wells):
            well_key = (plate, cell_type)
            well_number = next_well.get(well_key, 1)
            next_well[well_key] = well_number + 1
            row = {
                "Metadata_Plate": plate,
                "Metadata_Well": f"{'AB'[cell_type == 'lineB']}{well_number:02d}",
                "Metadata_cell_type": cell_type,
                "Metadata_treatment": treatment,
            }
            for feature in FEATURES:
                row[feature] = 0.0
            row[BASELINE_COLUMNS[cell_type]] = BASELINE
            if shifted:
                row[SHIFT_COLUMN] = SHIFT
            if treatment == REFERENCE_TREATMENT:
                offset = FIRST_REFERENCE_OFFSET + OFFSET_STEP * n_reference_wells
                n_reference_wells = n_reference_wells + 1
            else:
                offset = FIRST_TREATED_OFFSET + OFFSET_STEP * n_treated_wells
                n_treated_wells = n_treated_wells + 1
            row[OFFSET_COLUMNS[len(rows)]] = offset
            # differs between wells, so including it in scoring would change the rankings
            row[UNLISTED_FEATURE] = float(len(rows))
            rows.append(row)
    return pl.DataFrame(rows)


def treatments_fixture() -> pl.DataFrame:
    """profiles_treatments.parquet: see the module docstring."""
    profiles = build_profiles(
        [
            ("P1", "lineA", "DMSO", 8, False),
            ("P1", "lineA", "drugActive", 4, True),
            ("P1", "lineA", "drugInactive", 4, False),
            ("P1", "lineA", "drugSingle", 1, True),
            ("P2", "lineA", "DMSO", 8, False),
            ("P2", "lineA", "drugActive", 4, True),
            ("P2", "lineA", "drugInactive", 4, False),
        ]
    )
    # Blank out one value in the first drugActive well (as a null, as reference normalization
    # writes missing values), so that feature is dropped from scoring
    is_first_active = (pl.col("Metadata_Plate") == "P1") & (
        pl.col("Metadata_Well") == "A09"
    )
    return profiles.with_columns(
        pl.when(is_first_active)
        .then(None)
        .otherwise(pl.col(NAN_FEATURE))
        .alias(NAN_FEATURE)
    )


def pooled_cell_types_fixture() -> pl.DataFrame:
    """profiles_pooled_cell_types.parquet: see the module docstring."""
    return build_profiles(
        [
            ("P1", "lineA", "DMSO", 8, False),
            ("P1", "lineA", "drugA", 6, False),
            ("P1", "lineB", "DMSO", 8, False),
            ("P1", "lineB", "drugB", 6, True),
            ("P1", "lineA", "drugC", 3, True),
            ("P1", "lineB", "drugC", 3, True),
            ("P1", "lineA", "drugB", 1, True),
        ]
    )


def cell_types_fixture() -> pl.DataFrame:
    """profiles_cell_types.parquet: see the module docstring."""
    return build_profiles(
        [
            ("P1", "lineA", "DMSO", 8, False),
            ("P1", "lineA", "drugA", 6, True),
            ("P1", "lineB", "DMSO", 8, False),
            ("P1", "lineB", "drugA", 6, False),
        ]
    )


def main():
    treatments_fixture().write_parquet(FIXTURE_DIR / "profiles_treatments.parquet")
    pooled_cell_types_fixture().write_parquet(
        FIXTURE_DIR / "profiles_pooled_cell_types.parquet"
    )
    cell_types_fixture().write_parquet(FIXTURE_DIR / "profiles_cell_types.parquet")
    (FIXTURE_DIR / "features.txt").write_text("\n".join(FEATURES) + "\n")


if __name__ == "__main__":
    main()
