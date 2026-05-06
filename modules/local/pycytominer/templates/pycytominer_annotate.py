#!/usr/bin/env python3
"""
Nextflow template script to annotate a per-plate parquet file with platemap metadata using pycytominer.
The platemap is expected to be a tab-delimited file with a column matching the well identifier
in the parquet metadata (e.g. Metadata_Well).
The annotated output is written in parquet format.
"""

import importlib.metadata
import sys

import pandas as pd
import pycytominer


def write_versions(task_process):
    with open("versions.yml", "w") as f:
        f.write(f'"{task_process}":\\n')
        f.write(f"    python: {sys.version.split()[0]}\\n")
        f.write(f"    pycytominer: {importlib.metadata.version('pycytominer')}\\n")
        f.write(f"    pandas: {pd.__version__}\\n")


def main():
    # capture input variables from Nextflow
    platemap_file = "${platemap_file}"
    platemap_well_column = "${platemap_well_column}"
    parquet_file = "${parquet_file}"
    plate_id = "${meta.plate_id}"
    task_process = "${task.process}"

    platemap_df = pd.read_csv(platemap_file, sep="\\t", encoding="utf-8-sig")
    # cast to string after filling so numeric columns don't produce mixed types
    platemap_df = platemap_df.fillna("_NA_").astype(str)

    pycytominer.annotate(
        profiles=parquet_file,
        platemap=platemap_df,
        # need to add Metadata_ prefix because of pycytominer parsing
        join_on=[f"Metadata_{platemap_well_column}", "Metadata_Well"],
        output_file=f"{plate_id}_annotated.parquet",
        output_type="parquet",
    )

    write_versions(task_process)


if __name__ == "__main__":
    main()
