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

After annotation, redundant Metadata_*_N columns (cytotable artifacts from joining
Cells/Cytoplasm/Nuclei tables) are dropped when they agree with their un-suffixed base.
"""

import re
import sys
from importlib.metadata import version

import pandas as pd
import pycytominer

# A suffixed Metadata column is dropped if it disagrees with its base column in
# no more than this fraction of rows where both are non-null. 0.1% allows for
# segmentation edge cases (e.g. orphan nuclei) without keeping the column.
SUFFIXED_METADATA_MAX_MISMATCH_FRACTION = 0.001
SUFFIXED_METADATA_RE = re.compile(r"^(Metadata_.+)_(\d+)$")


def drop_redundant_suffixed_metadata(df: pd.DataFrame) -> pd.DataFrame:
    """Drop Metadata_*_N columns that match their un-suffixed base column.

    cytotable's join of Cells/Cytoplasm/Nuclei tables sometimes emits duplicate columns
    (e.g. Metadata_ObjectNumber_1, Metadata_ObjectNumber_2) when a column name
    appears in multiple object tables. When they agree with the base column they
    are redundant and can be dropped.
    """

    # go through columns to find potential suffixed columns
    # store them in a dict indexed by their base column
    # e.g., "Metadata_ObjectNumber_1" and "Metadata_ObjectNumber_2" would both be stored under "Metadata_ObjectNumber"
    candidates_by_base: dict[str, list[str]] = {}
    for col in df.columns:
        m = SUFFIXED_METADATA_RE.match(col)
        if m and m.group(1) in df.columns:
            candidates_by_base.setdefault(m.group(1), []).append(col)

    to_drop: list[str] = []
    for base, suffixed_cols in candidates_by_base.items():
        for suffix_col in suffixed_cols:
            base_na = df[base].isna()
            suffix_na = df[suffix_col].isna()

            # Rows where the suffix has data the base doesn't would be lost if we
            # dropped the column, so the column isn't safely redundant.
            n_suffix_only = int((base_na & ~suffix_na).sum())
            if n_suffix_only > 0:
                print(
                    f"  {suffix_col}: keeping ({n_suffix_only} rows have a value in "
                    f"{suffix_col} but null in {base})",
                    file=sys.stderr,
                )
                continue

            both_present = ~base_na & ~suffix_na
            n_both = int(both_present.sum())
            if n_both == 0:
                print(
                    f"  {suffix_col}: skipping (no overlapping non-null rows with {base})",
                    file=sys.stderr,
                )
                continue
            mismatches = int(
                (df.loc[both_present, base] != df.loc[both_present, suffix_col]).sum()
            )
            frac = mismatches / n_both
            if frac <= SUFFIXED_METADATA_MAX_MISMATCH_FRACTION:
                to_drop.append(suffix_col)
                if mismatches > 0:
                    print(
                        f"  {suffix_col}: dropping (differs from {base} in "
                        f"{mismatches}/{n_both} rows = {frac:.4%}, within "
                        f"{SUFFIXED_METADATA_MAX_MISMATCH_FRACTION:.1%} tolerance)",
                        file=sys.stderr,
                    )
            else:
                print(
                    f"  {suffix_col}: keeping (differs from {base} in "
                    f"{mismatches}/{n_both} rows = {frac:.2%}, exceeds "
                    f"{SUFFIXED_METADATA_MAX_MISMATCH_FRACTION:.1%} tolerance)",
                    file=sys.stderr,
                )

    if to_drop:
        print(
            f"Dropped {len(to_drop)} redundant suffixed Metadata columns: {to_drop}",
            file=sys.stderr,
        )
        df = df.drop(columns=to_drop)
    return df


def write_versions(task_process: str):
    with open("versions.yml", "w") as f:
        f.write(f'"{task_process}":\\n')
        f.write(f"    python: {sys.version.split()[0]}\\n")
        f.write(f"    pycytominer: {version('pycytominer')}\\n")
        f.write(f"    pandas: {version('pandas')}\\n")


def main():
    # capture input variables from Nextflow
    platemap_file = "${platemap_file}"
    platemap_well_column = "${platemap_well_column}"
    parquet_file = "${parquet_file}"
    plate_id = "${meta.plate_id}"
    task_process = "${task.process}"
    # merge the metadata column list during import from Nextflow to make them a string
    plate_metadata_columns = "${meta.plate_metadata_columns.join(',')}"

    platemap_df = pd.read_csv(
        platemap_file,
        sep="\\t",
        encoding="utf-8-sig",
        dtype_backend="numpy_nullable",
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
    # cast to pandas StringDtype first so NA is preserved across Int64/Float64/StringDtype columns,
    # then fill nulls with _NA_ so pycytominer doesn't drop them during annotation or aggregation
    platemap_df = platemap_df.astype("string").fillna("_NA_")

    try:
        annotated_df = pycytominer.annotate(
            profiles=parquet_file,
            platemap=platemap_df,
            # need to add Metadata_ prefix because of pycytominer parsing
            join_on=[f"Metadata_{platemap_well_column}", "Metadata_Well"],
        )
        annotated_df = drop_redundant_suffixed_metadata(annotated_df)
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
