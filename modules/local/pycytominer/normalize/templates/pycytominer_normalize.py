#!/usr/bin/env python3
"""
Nextflow template script to normalize profile features using pycytominer.

The input parquet is read with the pyarrow dtype backend so null values are preserved through
pycytominer. The normalized DataFrame is written back out as parquet.
"""

import sys
import warnings
from importlib.metadata import version

import pandas as pd
import pycytominer

# pyarrow-backed DataFrames trigger pandas' fragmentation warning; quiet it.
warnings.filterwarnings("ignore", category=pd.errors.PerformanceWarning)


def write_versions(task_process: str):
    with open("versions.yml", "w") as f:
        f.write(f'"{task_process}":\\n')
        f.write(f"    python: {sys.version.split()[0]}\\n")
        f.write(f"    pycytominer: {version('pycytominer')}\\n")
        f.write(f"    pandas: {version('pandas')}\\n")
        f.write(f"    pyarrow: {version('pyarrow')}\\n")


def main():
    # capture input variables from Nextflow
    parquet_file = "${parquet_file}"
    plate_id = "${meta.plate_id}"
    aggregate_level = "${meta.aggregate_level}"
    normalize_samples = "${normalize_samples}"
    normalize_method = "${normalize_method}"
    task_process = "${task.process}"

    # Read with pyarrow so null values are preserved through pycytominer.
    profiles_df = pd.read_parquet(parquet_file, dtype_backend="pyarrow")

    # pycytominer.normalize calls scipy's median_abs_deviation, which doesn't work with nullable types
    # convert non-metadata columns to floats
    feature_cols = [
        c
        for c in profiles_df.columns
        if not c.startswith("Metadata_")
        and pd.api.types.is_numeric_dtype(profiles_df[c])
    ]
    profiles_df[feature_cols] = profiles_df[feature_cols].astype("float64")

    normalized_df = pycytominer.normalize(
        profiles=profiles_df,
        features="infer",
        meta_features="infer",
        samples=normalize_samples,
        method=normalize_method,
    )

    normalized_df.to_parquet(
        f"{plate_id}_{aggregate_level}_normalized.parquet", index=False
    )

    write_versions(task_process)


if __name__ == "__main__":
    main()
