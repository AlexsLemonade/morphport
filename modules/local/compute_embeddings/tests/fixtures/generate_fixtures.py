#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "numpy==2.3.3",
#     "polars==1.35.2",
# ]
# ///
"""
Generate the small parquet fixtures for the compute_embeddings module tests.

Writes to the directory containing this script:

- profiles_P1.parquet, profiles_P2.parquet: per-well feature-selected profiles for
  plates P1 and P2 (6 wells each), with features feat_a..feat_e.
  - profiles_P1.parquet has Metadata_Plate, Metadata_Well, Metadata_cell_line, and
    Metadata_timepoint columns:
    - well A05: all of feat_a..feat_d are NaN (dropped with max_nan_fraction 0.5)
    - well A06: feat_b is NaN (filled with 0 with max_nan_fraction 0.5, dropped with 0)
  - profiles_P2.parquet has no Metadata_timepoint column, so it is null for P2 rows
    in the combined output
  - feat_e is NaN in every row and is not in features.txt, so including it by mistake
    would change the NaN counts in the summary
- profiles_all_nan.parquet: 3 wells of plate P3 with all of feat_a..feat_e NaN.
- features.txt: the selected features feat_a..feat_d, one per line.
- features_empty.txt: an empty feature list.

Usage (from the repository root):

    uv run modules/local/compute_embeddings/tests/fixtures/generate_fixtures.py
"""

import pathlib

import numpy as np
import polars as pl

FIXTURE_DIR = pathlib.Path(__file__).parent
FEATURES = ["feat_a", "feat_b", "feat_c", "feat_d"]
WELLS = [f"A{i:02d}" for i in range(1, 7)]


def write_profiles(
    metadata: dict, values: np.ndarray, file_name: str, n_rows: int
) -> None:
    pl.DataFrame(
        {
            **metadata,
            **{feature: values[:, i] for i, feature in enumerate(FEATURES)},
            "feat_e": [None] * n_rows,
        },
        schema_overrides={"feat_e": pl.Float64},
    ).write_parquet(FIXTURE_DIR / file_name)


def main():
    rng = np.random.default_rng(2026)
    n_wells = len(WELLS)

    # P1: one row with all features missing, one row with a single missing feature
    p1_values = rng.normal(size=(n_wells, len(FEATURES)))
    p1_values[4, :] = np.nan
    p1_values[5, 1] = np.nan
    p1_metadata = {
        "Metadata_Plate": ["P1"] * n_wells,
        "Metadata_Well": WELLS,
        "Metadata_cell_line": ["U2-OS"] * n_wells,
        "Metadata_timepoint": ["24h"] * n_wells,
    }
    write_profiles(p1_metadata, p1_values, "profiles_P1.parquet", n_wells)

    # P2: no missing features and no timepoint column
    p2_values = rng.normal(size=(n_wells, len(FEATURES)))
    p2_metadata = {
        "Metadata_Plate": ["P2"] * n_wells,
        "Metadata_Well": WELLS,
        "Metadata_cell_line": ["U2-OS"] * n_wells,
    }
    write_profiles(p2_metadata, p2_values, "profiles_P2.parquet", n_wells)

    # P3: every feature missing in every row
    n_all_nan = 3
    all_nan_values = np.full((n_all_nan, len(FEATURES)), np.nan)
    all_nan_metadata = {
        "Metadata_Plate": ["P3"] * n_all_nan,
        "Metadata_Well": WELLS[:n_all_nan],
        "Metadata_cell_line": ["U2-OS"] * n_all_nan,
        "Metadata_timepoint": ["24h"] * n_all_nan,
    }
    write_profiles(
        all_nan_metadata, all_nan_values, "profiles_all_nan.parquet", n_all_nan
    )

    # feature lists, as written by feature selection (one feature per line)
    (FIXTURE_DIR / "features.txt").write_text("\n".join(FEATURES) + "\n")
    (FIXTURE_DIR / "features_empty.txt").write_text("")


if __name__ == "__main__":
    main()
