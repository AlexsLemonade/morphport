#!/usr/bin/env python3
"""
Nextflow template script to deterministically drop columns from a parquet file

- Drop redundant Metadata_*_N columns that cytotable emits when joining
Cells/Cytoplasm/Nuclei tables, when those columns agree with their un-suffixed base.
- Drop CellProfiler feature columns in pycytominer's default blocklist via
pycytominer.feature_select(operation="blocklist").

"""

import re
import sys
import warnings
from importlib.metadata import version

import pandas as pd
import pycytominer

# pyarrow-backed DataFrames trigger pandas' fragmentation warning; quiet it.
warnings.filterwarnings("ignore", category=pd.errors.PerformanceWarning)

# A suffixed Metadata column is dropped if it disagrees with its base column in
# no more than this fraction of rows where both are non-null. 0.1% allows for
# segmentation edge cases (e.g. orphan nuclei) without keeping the column.
SUFFIXED_METADATA_MAX_MISMATCH_FRACTION = 0.001
SUFFIXED_METADATA_RE = re.compile(r"(Metadata_.+)_(\\d+)")


def drop_redundant_metadata(df: pd.DataFrame) -> pd.DataFrame:
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
        m = SUFFIXED_METADATA_RE.fullmatch(col)
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
        f.write(f"    pyarrow: {version('pyarrow')}\\n")


def main():
    # capture input variables from Nextflow
    parquet_file = "${parquet_file}"
    plate_id = "${meta.plate_id}"
    task_process = "${task.process}"

    # Read with pyarrow so null values are preserved through pycytominer
    profiles_df = pd.read_parquet(parquet_file, dtype_backend="pyarrow")

    profiles_df = drop_redundant_metadata(profiles_df)

    filtered_df = pycytominer.feature_select(
        profiles=profiles_df,
        operation="blocklist",
    )

    filtered_df.to_parquet(f"{plate_id}_blocklist_filtered.parquet", index=False)

    write_versions(task_process)


if __name__ == "__main__":
    main()
