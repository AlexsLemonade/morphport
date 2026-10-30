#!/usr/bin/env python3
"""
Nextflow template script to compute hierarchical clustering of the features
within one analysis group.

Reads the parquet files staged in the work directory, restricting to the
morphological features listed in features_file (written upstream by
pycytominer_feature_select), and runs scipy's hierarchical/agglomerative
clustering on the columns (features) of the feature matrix. Writes three files:

- clustering_features_linkage_{group_tag}.parquet: the scipy linkage matrix,
  one row per merge, with columns left, right, distance, n_members.
  Following scipy, leaf ids are 0..n-1 and the node created by row i is n + i.
  The full tree can be rebuilt with `scipy.cluster.hierarchy.to_tree()`.
- clustering_features_leaves_{group_tag}.parquet: one row per feature, with
  columns leaf_id, leaf_order (position in the dendrogram), and label (the
  feature name).
- clustering_features_summary_{group_tag}.json: clustering parameters and skip status.

Pairwise feature distances take memory proportional to the square of the
number of features, and time linear in the number of samples, so all groups
are clustered, including cell-level groups.

NOTE: features are clustered without any additional scaling. This module
assumes the upstream pycytominer normalization has already produced
approximately unit-variance features.
"""

import json
import pathlib
import sys
from importlib.metadata import version

import numpy as np
import polars as pl
from scipy.cluster.hierarchy import leaves_list, linkage

# Schemas for the output parquet files, used to write empty files for skipped groups
LINKAGE_SCHEMA = {
    "left": pl.Int64,
    "right": pl.Int64,
    "distance": pl.Float64,
    "n_members": pl.Int64,
}
LEAVES_SCHEMA = {
    "leaf_id": pl.Int64,
    "leaf_order": pl.Int64,
    "label": pl.String,
}


def validate_clustering_params(
    linkage_method: str, distance_metric: str, max_nan_fraction: float
):
    """
    Validate clustering parameters before loading any data, so bad params fail
    fast with a clear message instead of deep inside the real `linkage()` call.

    Delegates method/metric validation to scipy's own `linkage()` (via a tiny
    dummy call) rather than duplicating its list of valid methods and metrics.
    """
    # 3 points in 3 non-degenerate dimensions: enough for every method/metric
    # combination (e.g. 'correlation' is undefined on single-dimension points)
    # to run without a numerical error unrelated to param validity.
    dummy_points = [[1.0, 2.0, 3.0], [4.0, 1.0, 2.0], [0.0, 5.0, 1.0]]
    try:
        linkage(dummy_points, method=linkage_method, metric=distance_metric)
    except ValueError as e:
        raise ValueError(f"Invalid clustering parameters: {e}") from e
    if not 0.0 <= max_nan_fraction <= 1.0:
        raise ValueError(
            f"max_nan_fraction must be between 0 and 1, got {max_nan_fraction}"
        )


def write_versions(task_process: str):
    with open("versions.yml", "w") as f:
        f.write(f'"{task_process}":\\n')
        f.write(f"    python: {sys.version.split()[0]}\\n")
        f.write(f"    polars: {version('polars')}\\n")
        f.write(f"    numpy: {version('numpy')}\\n")
        f.write(f"    scipy: {version('scipy')}\\n")


def write_skipped(
    linkage_parquet: str,
    leaves_parquet: str,
    clustering_summary_json: str,
    summary: dict,
    reason: str,
):
    """
    Write empty linkage and leaves parquet files and a JSON summary for a group
    that was skipped due to being too small or other reasons.
    """
    pl.DataFrame(schema=LINKAGE_SCHEMA).write_parquet(linkage_parquet)
    pl.DataFrame(schema=LEAVES_SCHEMA).write_parquet(leaves_parquet)
    summary["skipped"] = True
    summary["skip_reason"] = reason
    with open(clustering_summary_json, "w") as f:
        json.dump(summary, f, indent=2)


def main():
    # ---- Capture Nextflow variables ----------------------------------------
    parquet_dir = pathlib.Path("input_parquet")
    features_file = pathlib.Path("${features_file}")
    # raw string so JSON escapes in metadata values survive
    group_meta = json.loads(r"""${meta_json}""")
    group_tag = group_meta["group_tag"]
    linkage_method = "${linkage_method}"
    distance_metric = "${distance_metric}"
    max_nan_fraction = float("${max_nan_fraction}")
    task_process = "${task.process}"
    linkage_parquet = f"clustering_features_linkage_{group_tag}.parquet"
    leaves_parquet = f"clustering_features_leaves_{group_tag}.parquet"
    clustering_summary_json = f"clustering_features_summary_{group_tag}.json"

    validate_clustering_params(linkage_method, distance_metric, max_nan_fraction)
    # written up front so skipped groups report versions too
    write_versions(task_process)

    grouping_column = group_meta["grouping_column"]
    subgroup_values = group_meta["subgroup_values"]

    summary = {
        "group_tag": group_tag,
        "aggregate_level": group_meta["aggregate_level"],
        "grouping_name": group_meta["grouping_name"],
        "grouping_column": grouping_column,
        "group": group_meta["group"],
        "subgroup_values": subgroup_values,
        "method": linkage_method,
        "metric": distance_metric,
        "max_nan_fraction": max_nan_fraction,
    }

    # ---- Load selected feature list ----------------------------------------
    # The upstream feature_select step writes one column name per line; the file
    # contains only morphological features (no Metadata_* columns).
    with open(features_file) as fh:
        feature_cols = [line.strip() for line in fh if line.strip()]
    if not feature_cols:
        raise RuntimeError(f"Feature list {features_file} is empty.")

    # stageAs places all input parquets in input_parquet/ (numbered to avoid name collisions).
    parquet_paths = list(parquet_dir.glob("*.parquet"))
    if not parquet_paths:
        raise RuntimeError(f"No input parquet files found in {parquet_dir}/.")

    # ---- Load and concatenate input parquets -------------------------------
    # Only the feature columns are needed; sample metadata is not used.
    feature_mat = (
        pl.concat(
            [pl.scan_parquet(p) for p in parquet_paths],
            # allow different parquets to have different columns; missing columns are filled with nulls
            how="diagonal_relaxed",
        )
        .select(feature_cols)
        .collect()
        .to_numpy()
        .astype(np.float64, copy=False)
    )
    n_rows_input = feature_mat.shape[0]

    # ---- Handle NaN rows -----------------------------------------------------
    # Per-row NaN handling, expressed as a fraction of feature columns
    # (matching compute_embeddings):
    #   * rows with nan_fraction > max_nan_fraction are dropped
    #   * surviving rows with any NaN have those entries filled with 0
    nan_per_row = np.isnan(feature_mat).sum(axis=1)
    keep_mask = nan_per_row <= max_nan_fraction * len(feature_cols)
    n_rows_dropped = int((~keep_mask).sum())
    n_rows_nan_filled = int(((nan_per_row > 0) & keep_mask).sum())
    feature_mat = np.nan_to_num(feature_mat[keep_mask], copy=False, nan=0.0)

    n_samples = feature_mat.shape[0]
    summary.update(
        {
            "n_rows_input": n_rows_input,
            "n_rows_dropped_nan": n_rows_dropped,
            "n_rows_nan_filled": n_rows_nan_filled,
            "n_samples": n_samples,
        }
    )
    if n_samples < 2:
        write_skipped(
            linkage_parquet,
            leaves_parquet,
            clustering_summary_json,
            summary,
            f"n_samples ({n_samples}) < 2 after NaN filtering; cannot cluster",
        )
        return

    # ---- Drop constant features --------------------------------------------
    # This is a backup: constant features should already have been removed by
    # feature selection (variance_threshold), but if any remain, their distances
    # to other features are undefined for some metrics (e.g. correlation)
    variable_mask = np.ptp(feature_mat, axis=0) > 0
    feature_cols = [f for f, keep in zip(feature_cols, variable_mask) if keep]
    feature_mat = feature_mat[:, variable_mask]
    n_features = len(feature_cols)
    summary["n_features_dropped_constant"] = int((~variable_mask).sum())
    summary["n_features"] = n_features
    if n_features < 2:
        write_skipped(
            linkage_parquet,
            leaves_parquet,
            clustering_summary_json,
            summary,
            f"n_features ({n_features}) < 2 after dropping constant features; cannot cluster",
        )
        return

    # ---- Cluster -------------------------------------------------------------
    # cluster the columns (features) by transposing the matrix
    linkage_matrix = linkage(
        feature_mat.T, method=linkage_method, metric=distance_metric
    )

    linkage_df = pl.DataFrame(
        {
            "left": linkage_matrix[:, 0],
            "right": linkage_matrix[:, 1],
            "distance": linkage_matrix[:, 2],
            "n_members": linkage_matrix[:, 3],
        },
        schema=LINKAGE_SCHEMA,
    )
    linkage_df.write_parquet(linkage_parquet)

    # ---- Write leaves table --------------------------------------------------
    # leaf ids are the column indices of the clustered feature matrix, matching
    # scipy's numbering; leaf_order is each leaf's position in the dendrogram
    leaf_order = np.argsort(leaves_list(linkage_matrix))
    leaves_df = pl.DataFrame(
        {
            "leaf_id": np.arange(n_features),
            "leaf_order": leaf_order,
            "label": feature_cols,
        },
        schema=LEAVES_SCHEMA,
    )
    leaves_df.write_parquet(leaves_parquet)

    with open(clustering_summary_json, "w") as f:
        json.dump(summary, f, indent=2)


if __name__ == "__main__":
    main()
