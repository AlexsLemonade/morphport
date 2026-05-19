#!/usr/bin/env python3
"""
Nextflow template script to select features using pycytominer.

Reads the parquet files staged in input_parquet/, concatenates them, applies
pycytominer.feature_select() using all samples, and writes the filtered parquet.

TODO: add per-channel correlation filtering — split features by channel suffix
(last underscore-delimited component for intensity/texture features), run
correlation_threshold independently per channel group, union kept features.
"""

import json
import sys
import warnings
from importlib.metadata import version

import pandas as pd
import pycytominer

warnings.filterwarnings("ignore", category=pd.errors.PerformanceWarning)


def write_versions(task_process: str):
    with open("versions.yml", "w") as f:
        f.write(f'"{task_process}":\\n')
        f.write(f"    python: {sys.version.split()[0]}\\n")
        f.write(f"    pycytominer: {version('pycytominer')}\\n")
        f.write(f"    pandas: {version('pandas')}\\n")
        f.write(f"    pyarrow: {version('pyarrow')}\\n")


def main():
    parquet_dir = "input_parquet"
    prefix = "${prefix}"
    operations = [op.strip() for op in "${operations}".split(",")]
    corr_threshold = float("${corr_threshold}")
    freq_cut = float("${freq_cut}")
    unique_cut = float("${unique_cut}")
    task_process = "${task.process}"

    df = pd.read_parquet(parquet_dir, dtype_backend="pyarrow")

    feature_cols = [
        c
        for c in df.columns
        if not c.startswith("Metadata_") and pd.api.types.is_numeric_dtype(df[c])
    ]
    df[feature_cols] = df[feature_cols].astype("float64")

    selected_df = pycytominer.feature_select(
        profiles=df,
        features="infer",
        samples="all",
        operation=operations,
        corr_threshold=corr_threshold,
        corr_method="pearson",
        freq_cut=freq_cut,
        unique_cut=unique_cut,
    )

    selected_feature_cols = [
        c
        for c in selected_df.columns
        if not c.startswith("Metadata_") and pd.api.types.is_numeric_dtype(selected_df[c])
    ]
    features_removed = sorted(set(feature_cols) - set(selected_feature_cols))

    n_features_in = len(feature_cols)
    n_features_out = len(selected_feature_cols)
    print(
        f"pycytominer_feature_select: {n_features_in} features in, "
        f"{n_features_out} features out "
        f"({n_features_in - n_features_out} removed) "
        f"using operations: {operations}",
        file=sys.stderr,
    )

    selected_df.to_parquet(f"{prefix}_feature_selected.parquet", index=False)

    grouping_info = json.loads("""${grouping_info}""")
    summary = {
        "prefix": prefix,
        "aggregate_level": grouping_info["aggregate_level"],
        "grouping_values": grouping_info["grouping_values"],
        "operations": operations,
        "corr_threshold": corr_threshold,
        "freq_cut": freq_cut,
        "unique_cut": unique_cut,
        "n_features_input": n_features_in,
        "n_features_output": n_features_out,
        "features_removed": features_removed,
    }
    with open(f"{prefix}_summary.json", "w") as f:
        json.dump(summary, f, indent=2)

    write_versions(task_process)


if __name__ == "__main__":
    main()
