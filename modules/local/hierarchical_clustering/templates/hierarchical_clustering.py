#!/usr/bin/env python3
"""
Nextflow template script to compute hierarchical clustering of the samples
within one analysis group.

Reads the PCA embeddings parquet written upstream by compute_embeddings,
selects the PC1..PCk columns, and runs scipy's hierarchical/agglomerative
clustering on them. Writes three files:

- clustering_linkage_{group_tag}.parquet: the scipy linkage matrix, one row per
  merge, with columns left, right, distance, n_members.
  Following scipy, leaf ids are 0..n-1 and the node created by row i is n + i.
  The full tree can be rebuilt with `scipy.cluster.hierarchy.to_tree()`.
- clustering_leaves_{group_tag}.parquet: one row per leaf, with columns leaf_id,
  leaf_order (position in the dendrogram), label, and identifying metadata.
- clustering_summary_{group_tag}.json: clustering parameters and skip status.
"""

import json
import re
import sys
from importlib.metadata import version

import numpy as np
import polars as pl
from scipy.cluster.hierarchy import leaves_list, linkage

# Identify the PC columns in the embeddings parquet by column name
PC_COLUMN_PATTERN = re.compile(r"PC[0-9]+")

# Columns that are used to identify a sample/well in the embeddings parquet.
# Together, Plate + Well identify a well-level sample; ImageNumber + ObjectNumber are
# needed in addition for cell-level samples, since ObjectNumber alone only identifies
# a cell within its image/site.
ID_COLUMNS = (
    "Metadata_Plate",
    "Metadata_Well",
    "Metadata_ImageNumber",
    "Metadata_ObjectNumber",
)

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
    linkage_method: str, distance_metric: str, max_samples: int
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
    if max_samples < 2:
        raise ValueError(f"max_samples must be >= 2, got {max_samples}")


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

    Falls back to the row index if no identifying columns survived into
    the embeddings parquet.
    """
    parts = [str(row[col]) for col in ID_COLUMNS if row.get(col) is not None]
    return "_".join(parts) if parts else str(row["__row_id__"])


def linkage_to_df(linkage_matrix: np.ndarray) -> pl.DataFrame:
    """
    Convert a scipy linkage matrix to a dataframe, casting the child ids and
    counts to integers.
    """
    return pl.DataFrame(
        {
            "left": linkage_matrix[:, 0],
            "right": linkage_matrix[:, 1],
            "distance": linkage_matrix[:, 2],
            "n_members": linkage_matrix[:, 3],
        },
        schema=LINKAGE_SCHEMA,
    )


def main():
    # ---- Capture Nextflow variables ----------------------------------------
    embeddings_parquet = "${embeddings_parquet}"
    summary_json = "${summary_json}"
    group_tag = "${group_tag}"
    # raw string so JSON escapes in metadata values survive
    group_meta = json.loads(r"""${meta_json}""")
    linkage_method = "${linkage_method}"
    distance_metric = "${distance_metric}"
    max_samples = int("${max_samples}")
    task_process = "${task.process}"
    linkage_parquet = f"clustering_linkage_{group_tag}.parquet"
    leaves_parquet = f"clustering_leaves_{group_tag}.parquet"
    clustering_summary_json = f"clustering_summary_{group_tag}.json"

    validate_clustering_params(linkage_method, distance_metric, max_samples)

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
    }

    # ---- Skip groups that compute_embeddings already skipped ---------------
    with open(summary_json) as fh:
        embeddings_summary = json.load(fh)
    if embeddings_summary.get("skipped"):
        write_skipped(
            linkage_parquet,
            leaves_parquet,
            clustering_summary_json,
            summary,
            f"upstream compute_embeddings skipped this group: {embeddings_summary.get('skip_reason', 'unknown reason')}",
        )
        write_versions(task_process)
        return

    # ---- Load embeddings and select PC columns ------------------------------
    df = pl.read_parquet(embeddings_parquet)
    pc_cols = [c for c in df.columns if PC_COLUMN_PATTERN.fullmatch(c)]
    if not pc_cols:
        write_skipped(
            linkage_parquet,
            leaves_parquet,
            clustering_summary_json,
            summary,
            "no PCA columns found in the embeddings parquet",
        )
        write_versions(task_process)
        return

    n_samples = df.height
    summary["n_samples"] = n_samples
    if n_samples < 2:
        write_skipped(
            linkage_parquet,
            leaves_parquet,
            clustering_summary_json,
            summary,
            f"n_samples ({n_samples}) < 2; cannot cluster",
        )
        write_versions(task_process)
        return
    if n_samples > max_samples:
        write_skipped(
            linkage_parquet,
            leaves_parquet,
            clustering_summary_json,
            summary,
            f"n_samples ({n_samples}) > clustering_max_samples ({max_samples})",
        )
        write_versions(task_process)
        return

    # ---- Build leaf labels + metadata ----------------------------------------
    # the grouping column (absent without an analysis groups file) and subgroup columns
    # identify each leaf's condition within the group; the ID columns identify the sample
    metadata_cols = (
        ([grouping_column] if grouping_column else [])
        + list(subgroup_values)
        + list(ID_COLUMNS)
    )
    # de-duplicate metadata columns while preserving order with `dict.fromkeys()`
    # and filter to only those that exist in the dataframe
    metadata_cols = [col for col in dict.fromkeys(metadata_cols) if col in df.columns]
    rows = (
        df.select(metadata_cols).to_dicts()
        if metadata_cols
        else [{} for _ in range(n_samples)]
    )
    labels = []
    for i, row in enumerate(rows):
        row["__row_id__"] = i
        labels.append(build_leaf_label(row))

    # ---- Cluster -------------------------------------------------------------
    pcs = df.select(pc_cols).to_numpy()
    linkage_matrix = linkage(pcs, method=linkage_method, metric=distance_metric)
    linkage_to_df(linkage_matrix).write_parquet(linkage_parquet)

    # ---- Write leaves table --------------------------------------------------
    # leaf ids are the row indices of the embeddings parquet, matching scipy's
    # numbering; leaf_order is each leaf's position in the dendrogram
    leaf_order = np.argsort(leaves_list(linkage_matrix))
    # build the leaf columns, then append the metadata columns from the
    # embeddings parquet (same row order) to the right with hstack
    leaves_df = pl.DataFrame(
        {
            "leaf_id": np.arange(n_samples),
            "leaf_order": leaf_order,
            "label": labels,
        },
        schema=LEAVES_SCHEMA,
    ).hstack(df.select(metadata_cols))
    leaves_df.write_parquet(leaves_parquet)

    with open(clustering_summary_json, "w") as f:
        json.dump(summary, f, indent=2)

    write_versions(task_process)


if __name__ == "__main__":
    main()
