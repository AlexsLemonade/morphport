#!/usr/bin/env python3
"""
Nextflow template script to compute PCA and UMAP embeddings for one analysis group.

Reads the parquet files staged in the work directory, drops Metadata_ columns
and non-numeric columns to build a measurement matrix, removes any rows with
NaN values in the feature columns, fits PCA, and then runs UMAP on the top PCs.
Writes one combined embeddings parquet (Metadata_* columns + PC1..PCk +
UMAP1 + UMAP2) and a JSON summary sidecar.

NOTE: features are passed to PCA without any additional scaling. This module
assumes the upstream pycytominer normalization has
already produced approximately unit-variance features.
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
from umap import UMAP  # noqa: E402


def write_versions(task_process: str):
    with open("versions.yml", "w") as f:
        f.write(f'"{task_process}":\\n')
        f.write(f"    python: {sys.version.split()[0]}\\n")
        f.write(f"    polars: {version('polars')}\\n")
        f.write(f"    numpy: {version('numpy')}\\n")
        f.write(f"    scikit-learn: {version('scikit-learn')}\\n")
        f.write(f"    umap-learn: {version('umap-learn')}\\n")


def main():
    # ---- Capture Nextflow variables ----------------------------------------
    prefix = "${prefix}"
    aggregate_level = "${meta.aggregate_level}"
    n_components_req = int("${pca_components}")
    n_neighbors = int("${umap_neighbors}")
    min_dist = float("${umap_min_dist}")
    random_seed = int("${random_seed}")
    max_nan_fraction = float("${max_nan_fraction}")
    min_rows = int("${min_rows}")
    task_cpus = int("${task.cpus}")
    task_process = "${task.process}"

    grouping_info = json.loads("""${grouping_info}""")
    grouping_values = grouping_info["grouping_values"]
    plate_ids = grouping_info["plate_ids"]

    # ---- Load and concatenate input parquets -------------------------------
    parquet_paths = sorted(str(p) for p in pathlib.Path(".").glob("*.parquet"))
    if not parquet_paths:
        raise RuntimeError("No input parquet files found in the working directory.")

    df = pl.read_parquet(parquet_paths)
    n_rows_input = df.height

    # ---- Split metadata vs. numeric measurement columns --------------------
    metadata_cols, feature_cols, dropped_cols = [], [], []
    for col, dtype in df.schema.items():
        if col.startswith("Metadata_"):
            metadata_cols.append(col)
        elif dtype.is_numeric():
            feature_cols.append(col)
        else:
            dropped_cols.append(col)
    if dropped_cols:
        print(
            f"compute_embeddings: dropping {len(dropped_cols)} non-numeric, "
            f"non-Metadata columns: {dropped_cols[:10]}"
            + ("..." if len(dropped_cols) > 10 else ""),
            file=sys.stderr,
        )
    if not feature_cols:
        raise RuntimeError(
            "No numeric measurement columns found after excluding Metadata_*."
        )

    # ---- Build feature matrix and handle NaN rows --------------------------
    # Extract features to numpy and keep metadata separately so we can free the
    # original DataFrame (which redundantly holds the feature columns).
    X = df.select(feature_cols).to_numpy()
    df_metadata = df.select(metadata_cols)
    del df

    # Per-row NaN handling, expressed as a fraction of feature columns:
    #   * rows with nan_fraction > max_nan_fraction are dropped
    #   * surviving rows with any NaN have those entries filled with 0
    # Default max_nan_fraction=0.0 reproduces strict "drop any row with a NaN".
    n_features = len(feature_cols)
    nan_per_row = np.isnan(X).sum(axis=1)
    nan_limit = max_nan_fraction * n_features
    keep_mask = nan_per_row <= nan_limit
    n_rows_dropped = int((~keep_mask).sum())
    n_rows_nan_filled = int(((nan_per_row > 0) & keep_mask).sum())
    if n_rows_dropped:
        print(
            f"compute_embeddings: dropping {n_rows_dropped} of {n_rows_input} rows "
            f"with NaN fraction greater than {max_nan_fraction:.4f}.",
            file=sys.stderr,
        )
    if n_rows_nan_filled:
        print(
            f"compute_embeddings: filling NaN with 0 in {n_rows_nan_filled} surviving rows "
            f"(each had NaN fraction <= {max_nan_fraction:.4f}).",
            file=sys.stderr,
        )
    df_metadata = df_metadata.filter(pl.Series(keep_mask))
    X = X[keep_mask]
    if n_rows_nan_filled:
        X = np.nan_to_num(X, copy=False, nan=0.0)

    n_rows_clean = X.shape[0]
    summary = {
        "prefix": prefix,
        "aggregate_level": aggregate_level,
        "grouping_values": grouping_values,
        "source_plate_ids": plate_ids,
        "n_plates": len(plate_ids),
        "n_parquets_read": len(parquet_paths),
        "n_rows_input": n_rows_input,
        "n_rows_dropped_nan": n_rows_dropped,
        "n_rows_nan_filled": n_rows_nan_filled,
        "max_nan_fraction": max_nan_fraction,
        "n_rows_output": int(n_rows_clean),
        "n_features_input": int(n_features),
        "n_dropped_non_numeric_columns": len(dropped_cols),
        "min_rows": min_rows,
    }

    # ---- Skip groups that are too small to embed ---------------------------
    if n_rows_clean < min_rows:
        skip_reason = (
            f"n_rows_clean ({n_rows_clean}) < min_rows ({min_rows})"
            if n_rows_clean > 0
            else "no rows survived NaN filtering"
        )
        print(
            f"compute_embeddings: skipping {prefix} - {skip_reason}; "
            "writing metadata-only parquet.",
            file=sys.stderr,
        )
        summary["skipped"] = True
        summary["skip_reason"] = skip_reason
        df_metadata.write_parquet(f"{prefix}_embeddings.parquet")
        with open(f"{prefix}_summary.json", "w") as f:
            json.dump(summary, f, indent=2)
        write_versions(task_process)
        return

    # ---- PCA, UMAP ---------------------------------------------------------
    # NOTE: no feature scaling is applied here.
    n_components_eff = max(1, min(n_components_req, n_features, n_rows_clean))
    pca = PCA(n_components=n_components_eff, random_state=random_seed)
    pcs = pca.fit_transform(X)

    # UMAP requires at least n_neighbors + 1 samples. If the group is small,
    # cap n_neighbors to keep UMAP runnable.
    n_neighbors_eff = max(2, min(n_neighbors, n_rows_clean - 1))
    umap_kwargs = dict(
        n_neighbors=n_neighbors_eff,
        min_dist=min_dist,
        n_components=2,
        random_state=random_seed,
        n_jobs=task_cpus,
    )
    try:
        umap_emb = UMAP(**umap_kwargs).fit_transform(pcs)
    except TypeError as exc:
        # UMAP's default spectral init calls ARPACK eigsh, which requires
        # k < N per connected component. Tiny groups (or k-NN graphs that
        # split into tiny components) hit "k >= N"; retry with random init.
        if "k >= N" not in str(exc):
            raise
        print(
            "compute_embeddings: spectral UMAP init failed for small data; "
            "falling back to random init.",
            file=sys.stderr,
        )
        umap_emb = UMAP(init="random", **umap_kwargs).fit_transform(pcs)

    # ---- Assemble output dataframe and write -------------------------------
    pc_names = [f"PC{i + 1}" for i in range(n_components_eff)]
    umap_names = ["UMAP1", "UMAP2"]
    frames = []
    if metadata_cols:
        frames.append(df_metadata)
    frames.append(pl.DataFrame(pcs, schema=pc_names))
    frames.append(pl.DataFrame(umap_emb, schema=umap_names))
    out_df = pl.concat(frames, how="horizontal")
    out_df.write_parquet(f"{prefix}_embeddings.parquet")

    summary["pca"] = {
        "n_components_requested": n_components_req,
        "n_components": n_components_eff,
        "explained_variance_ratio": pca.explained_variance_ratio_.tolist(),
    }
    summary["umap"] = {
        "n_neighbors_requested": n_neighbors,
        "n_neighbors": n_neighbors_eff,
        "min_dist": min_dist,
        "random_seed": random_seed,
    }
    with open(f"{prefix}_summary.json", "w") as f:
        json.dump(summary, f, indent=2)

    write_versions(task_process)


if __name__ == "__main__":
    main()
