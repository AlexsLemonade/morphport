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
    platemap_file = "${platemap_file}"
    platemap_well_column = "${platemap_well_column}"
    parquet_file = "${parquet_file}"
    plate_id = "${meta.plate_id}"
    task_process = "${task.process}"
    # merge the metadata column list during import from Nextflow to make them a string
    plate_metadata_columns = "${meta.plate_metadata_columns.join(',')}"

    # Read with pyarrow so null values are preserved through pycytominer.
    platemap_df = pd.read_csv(
        platemap_file,
        sep="\\t",
        encoding="utf-8-sig",
        dtype_backend="pyarrow",
    )

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
    # cast to string first so NA is preserved, then fill nulls with _NA_ so
    # pycytominer doesn't drop them during annotation or aggregation
    platemap_df = platemap_df.astype("string[pyarrow]").fillna("_NA_")

    # Read with pyarrow so null values are preserved through pycytominer.
    profiles_df = pd.read_parquet(parquet_file, dtype_backend="pyarrow")

    try:
        annotated_df = pycytominer.annotate(
            profiles=profiles_df,
            platemap=platemap_df,
            # need to add Metadata_ prefix because of pycytominer parsing
            join_on=[f"Metadata_{platemap_well_column}", "Metadata_Well"],
        )
        annotated_df.to_parquet(f"{plate_id}_annotated.parquet", index=False)
    except KeyError as ke:
        print("Error during pycytominer annotation.", file=sys.stderr)
        if ke.args[0] == f"Metadata_{platemap_well_column}:":
            print(
                f"The specified platemap_well_column '{platemap_well_column}' was not found in the platemap file.",
                file=sys.stderr,
            )
        elif ke.args[0] == "Metadata_Well":
            print(
                "The expected 'Metadata_Well' column was not found in the parquet file metadata.",
                file=sys.stderr,
            )
        else:
            raise

    write_versions(task_process)


if __name__ == "__main__":
    main()
