#!/usr/bin/env python3
"""
Nextflow template script to annotate a per-plate parquet file with platemap metadata using pycytominer.
If the plate is split across multiple parquet files, they are concatenated before annotation.
The platemap is expected to be a comma-delimited (.csv) or tab-delimited (.tsv or .txt) file
with a column matching the well identifier in the parquet metadata (e.g. Metadata_Well).
The delimiter is chosen by file extension, matching readPlatemap in the create_analysis_groups subworkflow.
The annotated output is written in parquet format.

All platemap metadata columns (except plate_map_name) are added to the parquet file by pycytominer annotate as 'Metadata_'-prefixed columns
We also ensure that all columns specified in the meta.plate_metadata_columns variable from Nextflow are present.
Any listed metadata column absent from the platemap is filled with '_Absent_' to ensure it is available for downstream steps.
Platemap values are read as literal text with whitespace stripped; blank values are filled with '_NA_' before merging.
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
    # directory where Nextflow stages the input parquet file(s) (stageAs in main.nf)
    parquet_dir = "input_parquet"
    plate_id = "${meta.plate_id}"
    task_process = "${task.process}"
    # merge the metadata column list during import from Nextflow to make them a string
    plate_metadata_columns = "${meta.plate_metadata_columns.join(',')}"

    # Read every platemap value as literal text so values match what Nextflow reads from the
    # same file when predicting analysis groups (enumeratePlatemapCombos): no type inference
    # (e.g. '010' stays '010', not 10.0), no NA-like strings ('NA', 'null') converted to nulls,
    # and surrounding whitespace stripped. Only blank cells become '_NA_'.
    # choose the delimiter by extension, matching platemapSeparator in Nextflow
    platemap_ext = platemap_file.rsplit(".", 1)[-1].lower()
    if platemap_ext == "csv":
        platemap_sep = ","
    elif platemap_ext in ("tsv", "txt"):
        platemap_sep = "\\t"
    else:
        raise ValueError(
            f"Platemap {platemap_file} must have a .csv, .tsv, or .txt extension."
        )
    platemap_df = pd.read_csv(
        platemap_file,
        sep=platemap_sep,
        encoding="utf-8-sig",
        dtype="string[pyarrow]",
        na_filter=False,
    )
    platemap_df.columns = [col.strip() for col in platemap_df.columns]
    platemap_df = (
        platemap_df.apply(lambda values: values.str.strip())
        # rows with missing trailing fields are filled with nulls despite na_filter=False
        .fillna("")
        .replace("", "_NA_")
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
    platemap_df = platemap_df.astype("string[pyarrow]")

    # Read with pyarrow so null values are preserved through pycytominer.
    # Reading the directory combines split parquet files into a single table.
    profiles_df = pd.read_parquet(parquet_dir, dtype_backend="pyarrow")

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
