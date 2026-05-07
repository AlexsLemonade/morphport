#!/usr/bin/env python3
"""
Nextflow template script to annotate a per-plate parquet file with platemap metadata using pycytominer.
The platemap is expected to be a tab-delimited file with a column matching the well identifier
in the parquet metadata (e.g. Metadata_Well).
The annotated output is written in parquet format.

All platemap metadata columns (except plate_map_name) are added to the parquet file by pycytominer annotate as 'Metadata_'-prefixed columns
We also ensure that all columns specified in the meta.plate_metadata_columns variable from Nextflow are present.
Any listed metadata column absent from the platemap is filled with '_Absent_' to ensure it is available for downstream steps.
All NULL values from the platemap are filled with '_NA_' before merging.
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
    # merge the metadata column list during import from Nextflow to make them a string
    plate_metadata_columns = "${meta.plate_metadata_columns.join(',')}"

    platemap_df = pd.read_csv(platemap_file, sep="\\t", encoding="utf-8-sig")

    # ensure all partition columns exist in the platemap before annotation,
    # either with raw names or without the Metadata_ prefix;
    # all will have the prefix added by pycytominer.annotate
    existing_cols = set(platemap_df.columns)
    for col in plate_metadata_columns.split(","):
        if (
            col not in existing_cols
            and col.removeprefix("Metadata_") not in existing_cols
        ):
            platemap_df[col] = "_Absent_"
    # fill any null values with _NA_ so pycytominer doesn't drop them during annotation or aggregation
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
