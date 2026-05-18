#!/usr/bin/env python3
"""
Nextflow template script to aggregate per-cell profiles into per-well bulk profiles using pycytominer.

The input parquet is read with the pyarrow dtype backend so null values are preserved through
pycytominer. The aggregated DataFrame is written back out as parquet.
"""

import sys
from importlib.metadata import version

import pandas as pd
import pycytominer


def write_versions(task_process: str):
    with open("versions.yml", "w") as f:
        f.write(f'"{task_process}":\\n')
        f.write(f"    python: {sys.version.split()[0]}\\n")
        f.write(f"    pycytominer: {version('pycytominer')}\\n")
        f.write(f"    pandas: {version('pandas')}\\n")


def main():
    # capture input variables from Nextflow
    parquet_file = "${parquet_file}"
    plate_id = "${meta.plate_id}"
    operation = "${operation}"
    task_process = "${task.process}"
    plate_metadata_columns = "${meta.plate_metadata_columns.join(',')}"

    # Build the strata list: plate, well, and any additional platemap-derived columns.
    # Ensure each column carries the Metadata_ prefix that pycytominer expects.
    extra = [c.strip() for c in plate_metadata_columns.split(",") if c.strip()]
    strata = ["Metadata_Plate", "Metadata_Well"] + [
        c if c.startswith("Metadata_") else f"Metadata_{c}" for c in extra
    ]

    # Read with pyarrow so null values are preserved through pycytominer.
    profiles_df = pd.read_parquet(parquet_file, dtype_backend="pyarrow")

    aggregated_df = pycytominer.aggregate(
        population_df=profiles_df,
        strata=strata,
        operation=operation,
    )

    aggregated_df.to_parquet(f"{plate_id}_bulk.parquet", index=False)

    write_versions(task_process)


if __name__ == "__main__":
    main()
