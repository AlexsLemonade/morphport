#!/usr/bin/env python3
"""
Nextflow template script to compute PCA and UMAP embeddings for one analysis group.

Reads the parquet files staged in the work directory, drops Metadata_ columns
and non-numeric columns to build a measurement matrix, removes any rows with
NaN values in the feature columns, standard-scales the features, fits PCA,
and then runs UMAP on the top PCs. Writes one combined embeddings parquet
(Metadata_* columns + PC1..PCk + UMAP1 + UMAP2) and a JSON summary sidecar.
"""

import json
import os
import pathlib
import sys
from importlib.metadata import version

# Set thread limits before numerical libraries import, so BLAS/OMP pick them up.
task_cpus_str = "${task.cpus}"
os.environ.setdefault("OMP_NUM_THREADS", task_cpus_str)
os.environ.setdefault("MKL_NUM_THREADS", task_cpus_str)
os.environ.setdefault("OPENBLAS_NUM_THREADS", task_cpus_str)
os.environ.setdefault("POLARS_MAX_THREADS", task_cpus_str)

import numpy as np  # noqa: E402
import polars as pl  # noqa: E402
from sklearn.decomposition import PCA  # noqa: E402
from sklearn.preprocessing import StandardScaler  # noqa: E402
from umap import UMAP  # noqa: E402


def write_versions(task_process: str):
    with open("versions.yml", "w") as f:
        f.write(f'"{task_process}":\\n')
        f.write(f"    python: {sys.version.split()[0]}\\n")
        f.write(f"    polars: {version('polars')}\\n")
        f.write(f"    numpy: {version('numpy')}\\n")
        f.write(f"    scikit-learn: {version('scikit-learn')}\\n")
        f.write(f"    umap-learn: {version('umap-learn')}\\n")


def parse_grouping_values(values_str: str) -> dict:
    if not values_str:
        return {}
    out = {}
    for piece in values_str.split(","):
        if "=" in piece:
            col, val = piece.split("=", 1)
            out[col] = val
    return out


def main():
    # ---- Capture Nextflow variables ----------------------------------------
    prefix = "${prefix}"
    aggregate_level = "${group_meta.aggregate_level}"
    grouping_cols_str = "${grouping_cols_str}"
    grouping_values_str = "${grouping_values_str}"
    plate_ids_str = "${plate_ids}"
    n_components_req = int("${n_components}")
    n_neighbors = int("${n_neighbors}")
    min_dist = float("${min_dist}")
    random_seed = int("${random_seed}")
    task_cpus = int("${task.cpus}")
    task_process = "${task.process}"

    grouping_cols = [c for c in grouping_cols_str.split(",") if c]
    grouping_values = parse_grouping_values(grouping_values_str)
    plate_ids = [p for p in plate_ids_str.split(",") if p]

    # ---- Load and concatenate input parquets -------------------------------
    parquet_paths = sorted(str(p) for p in pathlib.Path(".").glob("*.parquet"))
    if not parquet_paths:
        raise RuntimeError("No input parquet files found in the working directory.")

    df = pl.concat(
        [pl.read_parquet(p) for p in parquet_paths],
        how="vertical_relaxed",
    )
    n_rows_input = df.height

    # ---- Split metadata vs. numeric measurement columns --------------------
    metadata_cols = [c for c in df.columns if c.startswith("Metadata_")]
    candidate_cols = [c for c in df.columns if c not in metadata_cols]
    schema = df.schema
    feat_cols = [c for c in candidate_cols if schema[c].is_numeric()]
    dropped_cols = [c for c in candidate_cols if c not in feat_cols]
    if dropped_cols:
        print(
            f"compute_embeddings: dropping {len(dropped_cols)} non-numeric, "
            f"non-Metadata columns: {dropped_cols[:10]}"
            + ("..." if len(dropped_cols) > 10 else ""),
            file=sys.stderr,
        )
    if not feat_cols:
        raise RuntimeError(
            "No numeric measurement columns found after excluding Metadata_*."
        )

    # ---- Build feature matrix and drop NaN rows ----------------------------
    X = df.select(feat_cols).to_numpy()
    keep_mask = ~np.isnan(X).any(axis=1)
    n_rows_dropped = int((~keep_mask).sum())
    if n_rows_dropped:
        print(
            f"compute_embeddings: dropping {n_rows_dropped} of {n_rows_input} rows "
            f"with NaN values in feature columns.",
            file=sys.stderr,
        )
    df_clean = df.filter(pl.Series("__keep_mask", keep_mask))
    X_clean = X[keep_mask]

    if X_clean.shape[0] == 0:
        raise RuntimeError(
            "All rows contain NaN values in feature columns; nothing left to embed. "
            "Upstream normalization or feature curation should remove degenerate features."
        )

    # ---- Scale, PCA, UMAP --------------------------------------------------
    X_scaled = StandardScaler().fit_transform(X_clean)

    n_rows_clean, n_features = X_scaled.shape
    n_components_eff = max(1, min(n_components_req, n_features, n_rows_clean))
    pca = PCA(n_components=n_components_eff, random_state=random_seed)
    pcs = pca.fit_transform(X_scaled)

    # UMAP requires at least n_neighbors + 1 samples. If the group is small,
    # cap n_neighbors to keep UMAP runnable.
    n_neighbors_eff = max(2, min(n_neighbors, n_rows_clean - 1))
    umap = UMAP(
        n_neighbors=n_neighbors_eff,
        min_dist=min_dist,
        n_components=2,
        random_state=random_seed,
        n_jobs=task_cpus,
    )
    umap_emb = umap.fit_transform(pcs)

    # ---- Assemble output dataframe and write -------------------------------
    pc_names = [f"PC{i + 1}" for i in range(n_components_eff)]
    umap_names = ["UMAP1", "UMAP2"]
    frames = []
    if metadata_cols:
        frames.append(df_clean.select(metadata_cols))
    frames.append(pl.DataFrame(pcs, schema=pc_names))
    frames.append(pl.DataFrame(umap_emb, schema=umap_names))
    out_df = pl.concat(frames, how="horizontal")
    out_df.write_parquet(f"{prefix}_embeddings.parquet")

    summary = {
        "prefix": prefix,
        "aggregate_level": aggregate_level,
        "grouping_columns": grouping_cols,
        "grouping_values": grouping_values,
        "source_plate_ids": plate_ids,
        "n_plates": len(plate_ids),
        "n_parquets_read": len(parquet_paths),
        "n_rows_input": n_rows_input,
        "n_rows_dropped_nan": n_rows_dropped,
        "n_rows_output": int(X_clean.shape[0]),
        "n_features_input": int(n_features),
        "n_dropped_non_numeric_columns": len(dropped_cols),
        "pca": {
            "n_components_requested": n_components_req,
            "n_components": n_components_eff,
            "explained_variance_ratio": pca.explained_variance_ratio_.tolist(),
        },
        "umap": {
            "n_neighbors_requested": n_neighbors,
            "n_neighbors": n_neighbors_eff,
            "min_dist": min_dist,
            "random_seed": random_seed,
        },
    }
    with open(f"{prefix}_summary.json", "w") as f:
        json.dump(summary, f, indent=2)

    write_versions(task_process)


if __name__ == "__main__":
    main()
