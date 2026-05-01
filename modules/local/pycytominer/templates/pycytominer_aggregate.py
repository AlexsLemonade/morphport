#!/usr/bin/env python3
"""
Aggregates per-cell parquet data to well-level profiles using pycytominer.
Groups cells by Metadata_Plate and Metadata_Well, computes column-wise medians.
Output has one row per well.
"""

import importlib.metadata
import sys

import pandas as pd
import pycytominer

# These are always present if we have been through the pipeline up to this point
STRATA = ["Metadata_Plate", "Metadata_Well"]


def write_versions(task_process: str):
    with open("versions.yml", "w") as f:
        f.write(
            "\\n".join(
                [
                    f'"{task_process}":',
                    f"    python: {sys.version.split()[0]}",
                    f"    pycytominer: {importlib.metadata.version('pycytominer')}",
                    f"    pandas: {pd.__version__}",
                ]
            )
            + "\\n"
        )


def main():
    parquet_file = "${parquet_file}"
    plate_id = "${meta.plate_id}"
    task_process = "${task.process}"
    df = pd.read_parquet(parquet_file)

    pycytominer.aggregate(
        population_df=df,
        operation="median",
        strata=STRATA,
        output_file=f"{plate_id}_bulk.parquet",
        output_type="parquet",
    )

    write_versions(task_process)


if __name__ == "__main__":
    main()
