#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "numpy==2.3.3",
#     "polars==1.35.2",
# ]
# ///
"""
Generate the small parquet fixtures for the hierarchical_clustering_samples module tests.

Writes to the directory containing this script:

- profiles_nan_rows.parquet, profiles_constant_row.parquet: well-level feature-selected
  profiles for plates P1 and P2 (6 wells each) with Metadata_Plate, Metadata_Well,
  Metadata_cell_line, and Metadata_timepoint columns, and features feat_a..feat_e.
  - profiles_nan_rows.parquet (P1):
    - well A05: all of feat_a..feat_d are NaN (dropped with max_nan_fraction 0.5)
    - well A06: feat_b is NaN (filled with 0 with max_nan_fraction 0.5, dropped with 0)
  - profiles_constant_row.parquet (P2):
    - well A01: feat_a..feat_d are all equal (a zero-length row for the correlation metric)
  - feat_e is NaN in every row and is not in features.txt, so including it by mistake
    would change the NaN counts in the summary
- embeddings.parquet: 8 rows with the same metadata columns, PC1..PC3, UMAP1, and UMAP2.

The text and JSON fixtures (features.txt, embeddings_summary*.json) are written by hand.

Usage (from the repository root):

    uv run modules/local/hierarchical_clustering_samples/tests/fixtures/generate_fixtures.py
"""

import pathlib

import numpy as np
import polars as pl

FIXTURE_DIR = pathlib.Path(__file__).parent
FEATURES = ["feat_a", "feat_b", "feat_c", "feat_d"]
WELLS = [f"A{i:02d}" for i in range(1, 7)]


def metadata(plate: str, n_rows: int) -> dict:
    return {
        "Metadata_Plate": [plate] * n_rows,
        "Metadata_Well": WELLS[:n_rows],
        "Metadata_cell_line": ["U2-OS"] * n_rows,
        "Metadata_timepoint": ["24h"] * n_rows,
    }


def main():
    rng = np.random.default_rng(2026)

    # P1: one row with all features missing, one row with a single missing feature
    nan_rows = rng.normal(size=(len(WELLS), len(FEATURES)))
    nan_rows[4, :] = np.nan
    nan_rows[5, 1] = np.nan
    # P2: one constant row
    constant_row = rng.normal(size=(len(WELLS), len(FEATURES)))
    constant_row[0, :] = 1.0

    for plate, values, file_name in [
        ("P1", nan_rows, "profiles_nan_rows.parquet"),
        ("P2", constant_row, "profiles_constant_row.parquet"),
    ]:
        pl.DataFrame(
            {
                **metadata(plate, len(WELLS)),
                **{feature: values[:, i] for i, feature in enumerate(FEATURES)},
                "feat_e": [None] * len(WELLS),
            },
            schema_overrides={"feat_e": pl.Float64},
        ).write_parquet(FIXTURE_DIR / file_name)

    # embeddings: 4 wells from each plate, as written by compute_embeddings
    n_embeddings = 8
    embeddings_metadata = {
        key: metadata("P1", 4)[key] + metadata("P2", 4)[key]
        for key in metadata("P1", 4)
    }
    pcs = rng.normal(size=(n_embeddings, 3))
    umap = rng.normal(size=(n_embeddings, 2))
    pl.DataFrame(
        {
            **embeddings_metadata,
            "PC1": pcs[:, 0],
            "PC2": pcs[:, 1],
            "PC3": pcs[:, 2],
            "UMAP1": umap[:, 0],
            "UMAP2": umap[:, 1],
        }
    ).write_parquet(FIXTURE_DIR / "embeddings.parquet")


if __name__ == "__main__":
    main()
