#!/usr/bin/env python3
"""
Nextflow template script to deterministically drop columns from a parquet file

- Drops CellProfiler feature columns in pycytominer's default blocklist via
pycytominer.feature_select(operation="blocklist").

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
    task_process = "${task.process}"

    # Read with pyarrow so null values are preserved through pycytominer
    profiles_df = pd.read_parquet(parquet_file, dtype_backend="pyarrow")

    filtered_df = pycytominer.feature_select(
        profiles=profiles_df,
        operation="blocklist",
    )

    filtered_df.to_parquet(f"{plate_id}_blocklist_filtered.parquet", index=False)

    write_versions(task_process)


if __name__ == "__main__":
    main()
