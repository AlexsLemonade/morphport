#!/usr/bin/env python3
"""
Nextflow template script to compute hierarchical clustering of the samples
within one analysis group.

Reads the PCA embeddings parquet written upstream by compute_embeddings,
selects the PC1..PCk columns, and runs scipy's hierarchical/agglomerative
clustering on them. Writes a single JSON file containing a nested tree
(internal nodes carry a merge distance, leaf nodes carry a label and a small
amount of identifying metadata).
"""

import json
import re
import sys
from importlib.metadata import version

import polars as pl
from scipy.cluster.hierarchy import linkage, to_tree

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
    filename: str,
    group_tag: str,
    aggregate_level: str,
    grouping_values: dict,
    reason: str,
):
    """
    Write a JSON summary for a group that was skipped due to being too large or other reasons.
    """
    summary = {
        "group_tag": group_tag,
        "aggregate_level": aggregate_level,
        "grouping_values": grouping_values,
        "skipped": True,
        "skip_reason": reason,
    }
    with open(filename, "w") as f:
        json.dump(summary, f, indent=2)


def build_leaf_label(row: dict) -> str:
    """
    Build a label for a leaf node in the clustering tree from the identifying columns

    Falls back to the row index if no identifying columns survived into
    the embeddings parquet.
    """
    parts = [str(row[col]) for col in ID_COLUMNS if row.get(col) is not None]
    return "_".join(parts) if parts else str(row["__row_id__"])


def build_tree(node, labels, leaf_metadata):
    """
    Recursively build a nested dict representing the tree structure.
    """

    if node.is_leaf():
        entry = {"id": int(node.id), "label": labels[node.id]}
        metadata = leaf_metadata.get(node.id)
        if metadata:
            entry["metadata"] = metadata
        return entry
    return {
        "distance": float(node.dist),
        "children": [
            build_tree(node.get_left(), labels, leaf_metadata),
            build_tree(node.get_right(), labels, leaf_metadata),
        ],
    }


def main():
    # ---- Capture Nextflow variables ----------------------------------------
    embeddings_parquet = "${embeddings_parquet}"
    summary_json = "${summary_json}"
    group_tag = "${group_tag}"
    aggregate_level = "${meta.aggregate_level}"
    linkage_method = "${linkage_method}"
    distance_metric = "${distance_metric}"
    max_samples = int("${max_samples}")
    task_process = "${task.process}"
    output_file = f"clustering_{group_tag}.json"

    validate_clustering_params(linkage_method, distance_metric, max_samples)

    grouping_info = json.loads("""${grouping_info}""")
    grouping_values = grouping_info["grouping_values"]

    # ---- Skip groups that compute_embeddings already skipped ---------------
    with open(summary_json) as fh:
        embeddings_summary = json.load(fh)
    if embeddings_summary.get("skipped"):
        write_skipped(
            output_file,
            group_tag,
            aggregate_level,
            grouping_values,
            f"upstream compute_embeddings skipped this group: {embeddings_summary.get('skip_reason', 'unknown reason')}",
        )
        write_versions(task_process)
        return

    # ---- Load embeddings and select PC columns ------------------------------
    df = pl.read_parquet(embeddings_parquet)
    pc_cols = [c for c in df.columns if PC_COLUMN_PATTERN.fullmatch(c)]
    if not pc_cols:
        write_skipped(
            output_file,
            group_tag,
            aggregate_level,
            grouping_values,
            "no PCA columns found in the embeddings parquet",
        )
        write_versions(task_process)
        return

    n_samples = df.height
    if n_samples < 2:
        write_skipped(
            output_file,
            group_tag,
            aggregate_level,
            grouping_values,
            f"n_samples ({n_samples}) < 2; cannot cluster",
        )
        write_versions(task_process)
        return
    if n_samples > max_samples:
        write_skipped(
            output_file,
            group_tag,
            aggregate_level,
            grouping_values,
            f"n_samples ({n_samples}) > clustering_max_samples ({max_samples})",
        )
        write_versions(task_process)
        return

    # ---- Build leaf labels + metadata ----------------------------------------
    metadata_cols = [col for col in grouping_values] + list(ID_COLUMNS)
    # de-duplicate metadata columns while preserving order with `dict.fromkeys()`
    # and filter to only those that exist in the dataframe
    metadata_cols = [col for col in dict.fromkeys(metadata_cols) if col in df.columns]
    rows = (
        df.select(metadata_cols).to_dicts()
        if metadata_cols
        else [{} for _ in range(n_samples)]
    )
    labels = []
    leaf_metadata = {}
    for i, row in enumerate(rows):
        row["__row_id__"] = i
        labels.append(build_leaf_label(row))
        metadata = {col: row[col] for col in metadata_cols if row.get(col) is not None}
        if metadata:
            leaf_metadata[i] = metadata

    # ---- Cluster -------------------------------------------------------------
    pcs = df.select(pc_cols).to_numpy()
    tree_array = linkage(pcs, method=linkage_method, metric=distance_metric)

    # convert the linkage matrix to a tree structure and build a nested dict
    # representation of the tree, including leaf labels and metadata
    tree_root = to_tree(tree_array)
    tree = build_tree(tree_root, labels, leaf_metadata)

    cluster_result = {
        "group_tag": group_tag,
        "aggregate_level": aggregate_level,
        "grouping_values": grouping_values,
        "method": linkage_method,
        "metric": distance_metric,
        "n_samples": n_samples,
        "tree": tree,
    }
    with open(output_file, "w") as f:
        # no indent to prevent excessive indentation in files for large trees
        json.dump(cluster_result, f, indent=0)

    write_versions(task_process)


if __name__ == "__main__":
    main()
