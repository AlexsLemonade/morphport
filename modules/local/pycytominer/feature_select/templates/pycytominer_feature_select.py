#!/usr/bin/env python3
"""
Nextflow template script to select features using pycytominer.

Reads the parquet files staged in input_parquet/, applies
pycytominer.feature_select() with samples="all", and writes a text file listing
the kept morphological feature columns (one per line; Metadata_* columns are
not included — they are identified downstream by prefix).

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


# Operations whose results are unreliable or uncomputable when there are few rows.
STATS_OPS = frozenset(
    {
        "variance_threshold",
        "correlation_threshold",
        "drop_outliers",
        "noise_removal",
    }
)


def main():
    parquet_dir = "input_parquet"
    prefix = "${prefix}"
    operations = [op.strip() for op in "${operations}".split(",")]
    na_cutoff = float("${na_cutoff}")
    corr_threshold = float("${corr_threshold}")
    corr_method = "${corr_method}"
    freq_cut = float("${freq_cut}")
    unique_cut = float("${unique_cut}")
    min_rows_stats = int("${min_rows_stats}")
    outlier_cutoff = float("${outlier_cutoff}")
    noise_removal_perturb_groups = "${noise_removal_perturb_groups}"
    noise_removal_stdev_cutoff = float("${noise_removal_stdev_cutoff}")
    task_process = "${task.process}"

    df = pd.read_parquet(parquet_dir, dtype_backend="pyarrow")

    feature_cols = [
        c
        for c in df.columns
        if not c.startswith("Metadata_") and pd.api.types.is_numeric_dtype(df[c])
    ]
    df[feature_cols] = df[feature_cols].astype("float64")

    # Skip stats-based operations if there are too few rows to compute them reliably.
    # TODO: Should we just set the features here to be empty instead of skipping the filters?
    n_rows = df.shape[0]
    if n_rows < min_rows_stats:
        effective_ops = [op for op in operations if op not in STATS_OPS]
        dropped_ops = [op for op in operations if op in STATS_OPS]
        if dropped_ops:
            print(
                f"pycytominer_feature_select: only {n_rows} rows "
                f"(< min_rows_stats={min_rows_stats}); "
                f"skipping statistics-based operations {dropped_ops}.",
                file=sys.stderr,
            )
    else:
        effective_ops = operations

    if (
        "noise_removal" in effective_ops
        and noise_removal_perturb_groups not in df.columns
    ):
        raise ValueError(
            f"pycytominer_feature_select: noise_removal perturb-groups column "
            f"'{noise_removal_perturb_groups}' is not present in the input "
            f"parquet."
        )

    if effective_ops:
        selected_df = pycytominer.feature_select(
            profiles=df,
            features="infer",
            samples="all",
            operation=effective_ops,
            na_cutoff=na_cutoff,
            corr_threshold=corr_threshold,
            corr_method=corr_method,
            freq_cut=freq_cut,
            unique_cut=unique_cut,
            outlier_cutoff=outlier_cutoff,
            noise_removal_perturb_groups=noise_removal_perturb_groups,
            noise_removal_stdev_cutoff=noise_removal_stdev_cutoff,
        )
        selected_feature_cols = [
            c for c in feature_cols if c in set(selected_df.columns)
        ]
    else:
        # No operations to apply (e.g., all configured ops were stats ops and N is small).
        # Keep all features so downstream steps can still run.
        selected_feature_cols = list(feature_cols)

    n_features_in = len(feature_cols)
    n_features_out = len(selected_feature_cols)
    print(
        f"pycytominer_feature_select: {n_features_in} features in, "
        f"{n_features_out} features out "
        f"({n_features_in - n_features_out} removed) "
        f"using operations: {effective_ops}",
        file=sys.stderr,
    )

    with open(f"{prefix}_features.txt", "w") as f:
        for name in selected_feature_cols:
            f.write(f"{name}\\n")

    grouping_info = json.loads("""${grouping_info}""")
    summary = {
        "prefix": prefix,
        "aggregate_level": grouping_info["aggregate_level"],
        "grouping_values": grouping_info["grouping_values"],
        "operations_requested": operations,
        "operations_applied": effective_ops,
        "n_rows": n_rows,
        "min_rows_stats": min_rows_stats,
        "na_cutoff": na_cutoff,
        "corr_threshold": corr_threshold,
        "corr_method": corr_method,
        "freq_cut": freq_cut,
        "unique_cut": unique_cut,
        "outlier_cutoff": outlier_cutoff,
        "noise_removal_perturb_groups": noise_removal_perturb_groups,
        "noise_removal_stdev_cutoff": noise_removal_stdev_cutoff,
        "n_features_input": n_features_in,
        "n_features_output": n_features_out,
        "n_features_removed": n_features_in - n_features_out,
    }
    with open(f"{prefix}_summary.json", "w") as f:
        json.dump(summary, f, indent=2)

    write_versions(task_process)


if __name__ == "__main__":
    main()
