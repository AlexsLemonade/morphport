#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "numpy==2.3.3",
#     "polars==1.35.2",
# ]
# ///
"""
Generate the small parquet fixtures for the hierarchical_clustering_features module tests.

Writes to the directory containing this script:

- profiles_nan_rows.parquet, profiles_complete.parquet: per-well feature-selected
  profiles for plates P1 and P2 (6 wells each) with Metadata_Plate, Metadata_Well, and
  Metadata_cell_line columns, and features feat_a..feat_e and feat_constant.
  - profiles_nan_rows.parquet (P1):
    - well A05: all features are NaN (dropped with max_nan_fraction 0.5)
    - well A06: feat_b is NaN (filled with 0 with max_nan_fraction 0.5, dropped with 0)
  - profiles_complete.parquet (P2): no missing values
- profiles_single_well.parquet: one well (P3 A01) with the same columns and no missing values,
  too few samples to cluster.

In every file, feat_constant is 1.0 in every row (except the all-NaN well), so it is
dropped as a constant feature before clustering, and feat_e is NaN in every row and is
not in the features files, so including it by mistake would change the NaN counts in
the summary.

The text fixtures (features.txt, features_one_variable.txt) are written by hand.

Usage (from the repository root):

    uv run modules/local/hierarchical_clustering_features/tests/fixtures/generate_fixtures.py
"""

import pathlib

import numpy as np
import polars as pl

FIXTURE_DIR = pathlib.Path(__file__).parent
FEATURES = ["feat_a", "feat_b", "feat_c", "feat_d"]
WELLS = [f"A{i:02d}" for i in range(1, 7)]


def write_profiles(plate: str, values: np.ndarray, file_name: str):
    n_rows = values.shape[0]
    constant = np.ones(n_rows)
    # keep the all-NaN rows all NaN
    constant[np.isnan(values).all(axis=1)] = np.nan
    pl.DataFrame(
        {
            "Metadata_Plate": [plate] * n_rows,
            "Metadata_Well": WELLS[:n_rows],
            "Metadata_cell_line": ["U2-OS"] * n_rows,
            **{feature: values[:, i] for i, feature in enumerate(FEATURES)},
            "feat_constant": constant,
            "feat_e": [None] * n_rows,
        },
        schema_overrides={"feat_e": pl.Float64},
    ).write_parquet(FIXTURE_DIR / file_name)


def main():
    rng = np.random.default_rng(2026)

    # P1: one row with all features missing, one row with a single missing feature
    nan_rows = rng.normal(size=(len(WELLS), len(FEATURES)))
    nan_rows[4, :] = np.nan
    nan_rows[5, 1] = np.nan
    write_profiles("P1", nan_rows, "profiles_nan_rows.parquet")

    # P2: no missing values
    complete = rng.normal(size=(len(WELLS), len(FEATURES)))
    write_profiles("P2", complete, "profiles_complete.parquet")

    # P3: a single well
    single_well = rng.normal(size=(1, len(FEATURES)))
    write_profiles("P3", single_well, "profiles_single_well.parquet")


if __name__ == "__main__":
    main()
