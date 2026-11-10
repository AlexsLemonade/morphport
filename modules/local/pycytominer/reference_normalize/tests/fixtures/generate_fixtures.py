#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "numpy==2.3.3",
#     "polars==1.35.2",
# ]
# ///
"""
Generate the small parquet fixtures for the pycytominer reference_normalize module tests.

Writes these files to the directory containing this script:

- profiles_P1.parquet, profiles_P2.parquet, profiles_P3.parquet: per-well profiles of one cell
  type (lineA) on three plates, for comparing treatments within a cell type
  (comparison column Metadata_treatment, reference DMSO):
  - P1: 6 DMSO, 3 drugA, and 3 drugB wells (12 wells)
  - P2: 3 DMSO (fewer than 5, so a warning), 3 drugA, and 3 drugB wells (9 wells)
  - P3: 1 DMSO and 3 drugA wells (fewer than 2 reference wells, so all 4 wells are dropped)
- profiles_P1_DMSO.parquet, profiles_P1_drugA.parquet, ... (P1 and P2 only): the same wells
  split into one file per plate and treatment, as partitioning writes them
- profiles_two_cell_types.parquet: one plate (P1) with two cell types, for comparing cell types
  within a treatment (comparison column Metadata_cell_type, reference DMSO):
  lineA and lineB each have 4 DMSO and 3 drugA wells, and every lineB feature is offset by +100,
  so normalizing lineB to lineA's DMSO wells (or both to the pooled DMSO wells) would leave its
  DMSO wells far from zero.
- features.txt: the selected features (all features except Cells_AreaShape_Area, which tests
  that unlisted features are left out of the output).

Metadata columns: Metadata_Plate, Metadata_Well, Metadata_cell_type, Metadata_treatment.
Features (noise with a different offset and scale on each plate unless noted):

- Cells_AreaShape_Eccentricity
- Cells_Intensity_MeanIntensity_DNA: offset 10 and scale 3 on P2, so per-plate normalization matters
- Nuclei_AreaShape_Solidity: constant (0.9) in P2's DMSO wells, so zero reference spread on P2
- Cytoplasm_Intensity_MeanIntensity_Mito: missing in the first drugA well of P1 (NaN is kept)
- Cells_AreaShape_Area: not in features.txt

Usage (from the repository root):

    uv run modules/local/pycytominer/reference_normalize/tests/fixtures/generate_fixtures.py
"""

import pathlib

import numpy as np
import polars as pl

FIXTURE_DIR = pathlib.Path(__file__).parent
FEATURES = [
    "Cells_AreaShape_Eccentricity",
    "Cells_Intensity_MeanIntensity_DNA",
    "Nuclei_AreaShape_Solidity",
    "Cytoplasm_Intensity_MeanIntensity_Mito",
]
UNLISTED_FEATURE = "Cells_AreaShape_Area"
PLATE_SHIFT = {"P1": (0.0, 1.0), "P2": (10.0, 3.0), "P3": (5.0, 2.0)}

rng = np.random.default_rng(2026)


def plate_profiles(
    plate: str, cell_type: str, treatments: list[str], offset: float = 0.0
) -> pl.DataFrame:
    """
    Build per-well profiles for one cell type on one plate, one well per entry in treatments.

    offset is added to every feature, to make one cell type's profiles differ from another's.
    """
    # Each plate has its own shift and scale, standing in for plate-to-plate technical variation
    # that per-plate reference normalization should remove.
    shift, scale = PLATE_SHIFT[plate]
    n = len(treatments)
    data = {
        "Metadata_Plate": [plate] * n,
        # Wells are numbered in row A for lineA and row B for lineB, so that two cell types
        # on the same plate (profiles_two_cell_types.parquet) never share a well name.
        "Metadata_Well": [f"{'AB'[cell_type == 'lineB']}{i + 1:02d}" for i in range(n)],
        "Metadata_cell_type": [cell_type] * n,
        "Metadata_treatment": treatments,
    }
    # Every feature is standard normal noise, rescaled and shifted for the plate and cell type.
    # Treatments have no effect: these fixtures test normalization, not treatment differences.
    for feature in FEATURES + [UNLISTED_FEATURE]:
        data[feature] = (rng.normal(0, 1, n) * scale + shift + offset).tolist()
    df = pl.DataFrame(data)
    # Make Nuclei_AreaShape_Solidity constant in P2's DMSO (reference) wells, so it has zero
    # reference spread on that plate and is dropped from the whole group.
    if plate == "P2":
        df = df.with_columns(
            pl.when(pl.col("Metadata_treatment") == "DMSO")
            .then(0.9)
            .otherwise(pl.col("Nuclei_AreaShape_Solidity"))
            .alias("Nuclei_AreaShape_Solidity")
        )
    # Blank out one Mito value in the first drugA well of P1, to check that missing values
    # pass through normalization as missing. The drugB check limits this to the P1 file
    # for the treatment comparisons, leaving profiles_two_cell_types.parquet complete.
    if plate == "P1" and cell_type == "lineA" and "drugB" in treatments:
        first_drug = treatments.index("drugA")
        mito = df["Cytoplasm_Intensity_MeanIntensity_Mito"].to_list()
        mito[first_drug] = None
        df = df.with_columns(
            pl.Series(
                "Cytoplasm_Intensity_MeanIntensity_Mito", mito, dtype=pl.Float64
            ).fill_null(float("nan"))
        )
    return df


def main():
    treatments = {
        "P1": ["DMSO"] * 6 + ["drugA"] * 3 + ["drugB"] * 3,
        "P2": ["DMSO"] * 3 + ["drugA"] * 3 + ["drugB"] * 3,
        "P3": ["DMSO"] * 1 + ["drugA"] * 3,
    }
    for plate, plate_treatments in treatments.items():
        profiles = plate_profiles(plate, "lineA", plate_treatments)
        profiles.write_parquet(FIXTURE_DIR / f"profiles_{plate}.parquet")
        # the same wells split into one file per treatment, as partitioning writes them
        if plate in ("P1", "P2"):
            for treatment in dict.fromkeys(plate_treatments):
                profiles.filter(
                    pl.col("Metadata_treatment") == treatment
                ).write_parquet(FIXTURE_DIR / f"profiles_{plate}_{treatment}.parquet")

    two_cell_types = ["DMSO"] * 4 + ["drugA"] * 3
    pl.concat(
        [
            plate_profiles("P1", "lineA", two_cell_types),
            plate_profiles("P1", "lineB", two_cell_types, offset=100.0),
        ]
    ).write_parquet(FIXTURE_DIR / "profiles_two_cell_types.parquet")

    (FIXTURE_DIR / "features.txt").write_text("\n".join(FEATURES) + "\n")


if __name__ == "__main__":
    main()
