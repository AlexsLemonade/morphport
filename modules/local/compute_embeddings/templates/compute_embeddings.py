#!/usr/bin/env python3
"""
Nextflow template script to compute PCA and UMAP embeddings for one analysis group.

Reads the parquet files staged in the work directory, restricting to the
Metadata_* columns plus the morphological features listed in features_file
(written upstream by pycytominer_feature_select).
Removes any rows with NaN values in the feature columns greater than a threshold,
fits PCA, and then runs UMAP on the top PCs.

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
import tempfile
from importlib.metadata import version

import numpy as np
import polars as pl
from sklearn.decomposition import PCA

# umap triggers numba-jitted compilation on import; numba's default caching
# tries to write cache files next to the installed umap module, which fails
# when running as an unmapped container uid with a read-only site-packages
os.environ.setdefault("NUMBA_CACHE_DIR", tempfile.gettempdir())
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
    parquet_dir = pathlib.Path("input_parquet")
    features_file = pathlib.Path("${features_file}")
    group_tag = "${group_tag}"
    # raw string so JSON escapes in metadata values survive
    group_meta = json.loads(r"""${meta_json}""")
    aggregate_level = group_meta["aggregate_level"]
    plate_ids = group_meta["plate_ids"]
    n_components_req = int("${pca_components}")
    n_neighbors = int("${umap_neighbors}")
    min_dist = float("${umap_min_dist}")
    random_seed = int("${random_seed}")
    max_nan_fraction = float("${max_nan_fraction}")
    min_rows = int("${min_rows}")
    task_cpus = int("${task.cpus}")
    task_process = "${task.process}"

    # ---- Load selected feature list ----------------------------------------
    # The upstream feature_select step writes one column name per line; the file
    # contains only morphological features (no Metadata_* columns).
    with open(features_file) as fh:
        feature_cols = [line.strip() for line in fh if line.strip()]
    if not feature_cols:
        raise RuntimeError(f"Feature list {features_file} is empty.")

    # ---- Load and concatenate input parquets -------------------------------
    # stageAs places all input parquets in input_parquet/ (numbered to avoid name collisions).
    parquet_paths = list(parquet_dir.glob("*.parquet"))
    if not parquet_paths:
        raise RuntimeError(f"No input parquet files found in {parquet_dir}/.")

    # Identify metadata columns by prefix. Different parquets may carry
    # different Metadata_* columns (e.g. only some plates have
    # Metadata_treatment), so keep the union of all metadata columns across
    # all parquets.
    # use dict.fromkeys to preserve order while removing duplicates
    metadata_cols = list(
        dict.fromkeys(
            c
            for p in parquet_paths
            for c in pl.read_parquet_schema(p)
            if c.startswith("Metadata_")
        )
    )

    df = (
        pl.concat(
            [pl.scan_parquet(p) for p in parquet_paths],
            # allow different parquets to have different columns; missing columns are filled with nulls
            how="diagonal_relaxed",
        )
        .select(metadata_cols + feature_cols)
        .collect()
    )
    n_rows_input = df.height

    # ---- Build feature matrix and handle NaN rows --------------------------
    # Extract features to numpy and keep metadata separately so we can free the
    # original DataFrame (which redundantly holds the feature columns).
    feature_mat = df.select(feature_cols).to_numpy()
    df_metadata = df.select(metadata_cols)
    del df

    # Per-row NaN handling, expressed as a fraction of feature columns:
    #   * rows with nan_fraction > max_nan_fraction are dropped
    #   * surviving rows with any NaN have those entries filled with 0
    # Default max_nan_fraction=0.0 reproduces strict "drop any row with a NaN".
    n_features = len(feature_cols)
    nan_per_row = np.isnan(feature_mat).sum(axis=1)
    nan_limit = max_nan_fraction * n_features
    keep_mask = nan_per_row <= nan_limit

    n_rows_dropped = int((~keep_mask).sum())
    n_rows_nan_filled = int(((nan_per_row > 0) & keep_mask).sum())

    df_metadata = df_metadata.filter(pl.Series(keep_mask))
    feature_mat = feature_mat[keep_mask]
    if n_rows_dropped:
        print(
            f"compute_embeddings: dropping {n_rows_dropped} of {n_rows_input} rows "
            f"with NaN fraction greater than {max_nan_fraction:.4f}.",
            file=sys.stderr,
        )
    if n_rows_nan_filled:
        feature_mat = np.nan_to_num(feature_mat, copy=False, nan=0.0)
        print(
            f"compute_embeddings: filling NaN with 0 in {n_rows_nan_filled} surviving rows "
            f"(each had NaN fraction <= {max_nan_fraction:.4f}).",
            file=sys.stderr,
        )

    n_rows_clean = feature_mat.shape[0]
    summary = {
        "group_tag": group_tag,
        "aggregate_level": aggregate_level,
        "grouping_name": group_meta["grouping_name"],
        "grouping_column": group_meta["grouping_column"],
        "group": group_meta["group"],
        "subgroup_values": group_meta["subgroup_values"],
        "source_plate_ids": plate_ids,
        "n_plates": len(plate_ids),
        "n_parquets_read": len(parquet_paths),
        "n_rows_input": n_rows_input,
        "n_rows_dropped_nan": n_rows_dropped,
        "n_rows_nan_filled": n_rows_nan_filled,
        "max_nan_fraction": max_nan_fraction,
        "n_rows_output": n_rows_clean,
        "n_features_input": n_features,
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
            f"compute_embeddings: skipping {group_tag} - {skip_reason}; "
            "writing metadata-only parquet.",
            file=sys.stderr,
        )
        summary["skipped"] = True
        summary["skip_reason"] = skip_reason
        df_metadata.write_parquet(f"embeddings_{group_tag}.parquet")
        with open(f"embeddings_summary_{group_tag}.json", "w") as f:
            json.dump(summary, f, indent=2)
        write_versions(task_process)
        return

    # ---- PCA, UMAP ---------------------------------------------------------
    # NOTE: no feature scaling is applied here.
    n_components_eff = max(1, min(n_components_req, n_features, n_rows_clean))
    pca = PCA(n_components=n_components_eff, random_state=random_seed)
    pcs = pca.fit_transform(feature_mat)

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
    out_df.write_parquet(f"embeddings_{group_tag}.parquet")

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
    with open(f"embeddings_summary_{group_tag}.json", "w") as f:
        json.dump(summary, f, indent=2)

    write_versions(task_process)


if __name__ == "__main__":
    main()
