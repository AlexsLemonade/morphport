#!/usr/bin/env python3
"""
Nextflow template script to compute hierarchical clustering of the samples
within one analysis group.

Clusters the samples (rows) of one of two inputs:

- the feature-selected profiles, restricted to the morphological features
  listed in features_file (written upstream by pycytominer_feature_select), or
- the PC1..PCk columns of the PCA embeddings parquet written upstream by
  compute_embeddings, when no features file is given.

Clustering always uses Ward linkage via fastcluster's memory-saving
`linkage_vector()`, which never builds the pairwise distance matrix, so memory
scales with the size of the input matrix rather than the square of the number
of samples. Ward linkage requires euclidean distances; the correlation and
cosine metrics are implemented by transforming each row before clustering:

- correlation: center each row, then scale it to unit length
- cosine: scale each row to unit length
- euclidean: no transformation

For unit-length rows, squared euclidean distance is 2 * (1 - cosine similarity),
which is 2 * (1 - Pearson correlation) when the rows are also centered.

Writes three files:

- clustering_samples_linkage_{group_tag}.parquet: the scipy linkage matrix, one row per
  merge, with columns left, right, distance, n_members.
  Following scipy, leaf ids are 0..n-1 and the node created by row i is n + i.
  The full tree can be rebuilt with `scipy.cluster.hierarchy.to_tree()`.
- clustering_samples_leaves_{group_tag}.parquet: one row per leaf, with columns leaf_id,
  leaf_order (position in the dendrogram), label, and identifying metadata.
- clustering_samples_summary_{group_tag}.json: clustering parameters and skip status.
"""

import json
import pathlib
import re
import sys
from importlib.metadata import version

import fastcluster
import numpy as np
import polars as pl
from scipy.cluster.hierarchy import leaves_list

# Identify the PC columns in the embeddings parquet by column name
PC_COLUMN_PATTERN = re.compile(r"PC[0-9]+")

# Columns that are used to identify a sample/well.
# Together, Plate + Well identify a well-level sample; ImageNumber + ObjectNumber are
# needed in addition for cell-level samples, since ObjectNumber alone only identifies
# a cell within its image/site.
ID_COLUMNS = (
    "Metadata_Plate",
    "Metadata_Well",
    "Metadata_ImageNumber",
    "Metadata_ObjectNumber",
)

# Distance metrics supported by transforming rows before Ward/euclidean clustering
DISTANCE_METRICS = ("correlation", "cosine", "euclidean")

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
    distance_metric: str, max_nan_fraction: float, max_samples: int
):
    """
    Validate clustering parameters before loading any data, so bad params fail
    fast with a clear message.
    """
    if distance_metric not in DISTANCE_METRICS:
        raise ValueError(
            f"distance_metric must be one of {', '.join(DISTANCE_METRICS)}, got '{distance_metric}'"
        )
    if not 0.0 <= max_nan_fraction <= 1.0:
        raise ValueError(
            f"max_nan_fraction must be between 0 and 1, got {max_nan_fraction}"
        )
    if max_samples < 2:
        raise ValueError(f"max_samples must be >= 2, got {max_samples}")


def write_versions(task_process: str):
    with open("versions.yml", "w") as f:
        f.write(f'"{task_process}":\\n')
        f.write(f"    python: {sys.version.split()[0]}\\n")
        f.write(f"    polars: {version('polars')}\\n")
        f.write(f"    numpy: {version('numpy')}\\n")
        f.write(f"    scipy: {version('scipy')}\\n")
        f.write(f"    fastcluster: {version('fastcluster')}\\n")


def write_skipped(
    linkage_parquet: str,
    leaves_parquet: str,
    clustering_summary_json: str,
    summary: dict,
    reason: str,
):
    """
    Write empty linkage and leaves parquet files and a JSON summary for a group
    that was skipped due to being too large or other reasons.
    """
    pl.DataFrame(schema=LINKAGE_SCHEMA).write_parquet(linkage_parquet)
    pl.DataFrame(schema=LEAVES_SCHEMA).write_parquet(leaves_parquet)
    summary["skipped"] = True
    summary["skip_reason"] = reason
    with open(clustering_summary_json, "w") as f:
        json.dump(summary, f, indent=2)


def build_leaf_label(row: dict) -> str:
    """
    Build a label for a leaf node in the clustering tree from the identifying columns

    Falls back to the row index if no identifying columns are present.
    """
    parts = [str(row[col]) for col in ID_COLUMNS if row.get(col) is not None]
    return "_".join(parts) if parts else str(row["__row_id__"])


def transform_rows(mat: np.ndarray, distance_metric: str) -> tuple[np.ndarray, int]:
    """
    Transform the rows of mat so that euclidean distances between them
    correspond to the requested distance metric.

    Returns the transformed matrix and the number of rows with zero length
    (e.g. constant rows for correlation), which are left as all zeros.
    """
    if distance_metric == "euclidean":
        return mat, 0
    if distance_metric == "correlation":
        mat = mat - mat.mean(axis=1, keepdims=True)
    norms = np.linalg.norm(mat, axis=1, keepdims=True)
    nonzero = norms > 0
    mat = np.divide(mat, norms, out=np.zeros_like(mat), where=nonzero)
    return mat, int((~nonzero).sum())

def main():
    # ---- Capture Nextflow variables ----------------------------------------
    parquet_dir = pathlib.Path("input_parquet")
    features_file = "${features_file_name}"
    embeddings_summary_json = "${embeddings_summary_name}"
    # raw string so JSON escapes in metadata values survive
    group_meta = json.loads(r"""${meta_json}""")
    group_tag = group_meta["group_tag"]
    distance_metric = "${distance_metric}"
    max_nan_fraction = float("${max_nan_fraction}")
    max_samples = int("${max_samples}")
    task_process = "${task.process}"
    linkage_parquet = f"clustering_samples_linkage_{group_tag}.parquet"
    leaves_parquet = f"clustering_samples_leaves_{group_tag}.parquet"
    clustering_summary_json = f"clustering_samples_summary_{group_tag}.json"

    validate_clustering_params(distance_metric, max_nan_fraction, max_samples)
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
        "input": "features" if features_file else "pcs",
        "method": "ward",
        "metric": distance_metric,
        "max_nan_fraction": max_nan_fraction,
    }

    # ---- Skip groups that compute_embeddings already skipped ---------------
    if embeddings_summary_json:
        with open(embeddings_summary_json) as fh:
            embeddings_summary = json.load(fh)
        if embeddings_summary.get("skipped"):
            write_skipped(
                linkage_parquet,
                leaves_parquet,
                clustering_summary_json,
                summary,
                f"upstream compute_embeddings skipped this group: {embeddings_summary.get('skip_reason', 'unknown reason')}",
            )
            return

    # ---- Load input parquets -------------------------------------------------
    # stageAs places all input parquets in input_parquet/ (numbered to avoid name collisions);
    # sort so the row (leaf) order is deterministic
    parquet_paths = sorted(parquet_dir.glob("*.parquet"))
    if not parquet_paths:
        raise RuntimeError(f"No input parquet files found in {parquet_dir}/.")
    input_lf = pl.concat(
        [pl.scan_parquet(p) for p in parquet_paths],
        # allow different parquets to have different columns; missing columns are filled with nulls
        how="diagonal_relaxed",
    )
    input_columns = input_lf.collect_schema().names()

    # ---- Select the columns to cluster on -----------------------------------
    if features_file:
        # The upstream feature_select step writes one column name per line; the file
        # contains only morphological features (no Metadata_* columns).
        with open(features_file) as fh:
            cluster_cols = [line.strip() for line in fh if line.strip()]
        if not cluster_cols:
            raise RuntimeError(f"Feature list {features_file} is empty.")
    else:
        cluster_cols = [c for c in input_columns if PC_COLUMN_PATTERN.fullmatch(c)]
        if not cluster_cols:
            write_skipped(
                linkage_parquet,
                leaves_parquet,
                clustering_summary_json,
                summary,
                "no PCA columns found in the embeddings parquet",
            )
            return
    summary["n_columns"] = len(cluster_cols)

    # the grouping column (absent without an analysis groups file) and subgroup columns
    # identify each leaf's condition within the group; the ID columns identify the sample
    metadata_cols = (
        ([grouping_column] if grouping_column else [])
        + list(subgroup_values)
        + list(ID_COLUMNS)
    )
    # de-duplicate metadata columns while preserving order with `dict.fromkeys()`
    # and filter to only those that exist in the input
    metadata_cols = [
        col for col in dict.fromkeys(metadata_cols) if col in input_columns
    ]

    input_df = input_lf.select(metadata_cols + cluster_cols).collect()
    cluster_mat = (
        input_df.select(cluster_cols).to_numpy().astype(np.float64, copy=False)
    )
    n_rows_input = cluster_mat.shape[0]

    # ---- Handle NaN rows -----------------------------------------------------
    # Per-row NaN handling, expressed as a fraction of clustered columns
    # (matching compute_embeddings and hierarchical_clustering_features):
    #   * rows with nan_fraction > max_nan_fraction are dropped
    #   * surviving rows with any NaN have those entries filled with 0
    nan_per_row = np.isnan(cluster_mat).sum(axis=1)
    keep_mask = nan_per_row <= max_nan_fraction * len(cluster_cols)
    n_rows_dropped = int((~keep_mask).sum())
    n_rows_nan_filled = int(((nan_per_row > 0) & keep_mask).sum())
    cluster_mat = np.nan_to_num(cluster_mat[keep_mask], nan=0.0)
    metadata_df = input_df.select(metadata_cols).filter(pl.Series(keep_mask))
    del input_df

    n_samples = cluster_mat.shape[0]
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
    if n_samples > max_samples:
        write_skipped(
            linkage_parquet,
            leaves_parquet,
            clustering_summary_json,
            summary,
            f"n_samples ({n_samples}) > clustering_max_samples ({max_samples})",
        )
        return

    # ---- Build leaf labels ---------------------------------------------------
    rows = metadata_df.to_dicts() if metadata_cols else [{} for _ in range(n_samples)]
    labels = []
    for i, row in enumerate(rows):
        row["__row_id__"] = i
        labels.append(build_leaf_label(row))

    # ---- Cluster -------------------------------------------------------------
    cluster_mat, n_zero_length = transform_rows(cluster_mat, distance_metric)
    summary["n_samples_zero_length"] = n_zero_length
    # linkage_vector requires a C-contiguous float64 array
    cluster_mat = np.ascontiguousarray(cluster_mat, dtype=np.float64)
    linkage_matrix = fastcluster.linkage_vector(
        cluster_mat, method="ward", metric="euclidean"
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
    # leaf ids are the row indices of the clustered matrix (input rows after NaN
    # filtering), matching scipy's numbering; leaf_order is each leaf's position
    # in the dendrogram
    leaf_order = np.argsort(leaves_list(linkage_matrix))
    # build the leaf columns, then append the metadata columns (same row order)
    # to the right with hstack
    leaves_df = pl.DataFrame(
        {
            "leaf_id": np.arange(n_samples),
            "leaf_order": leaf_order,
            "label": labels,
        },
        schema=LEAVES_SCHEMA,
    ).hstack(metadata_df)
    leaves_df.write_parquet(leaves_parquet)

    with open(clustering_summary_json, "w") as f:
        json.dump(summary, f, indent=2)


if __name__ == "__main__":
    main()
