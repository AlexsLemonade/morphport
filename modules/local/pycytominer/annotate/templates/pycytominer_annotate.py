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

After annotation, metadata columns that duplicate each other are checked:
- Columns whose names differ only by the _platemap suffix pycytominer adds on name collisions,
  the numeric suffix (_1, _2, ...) DuckDB adds to repeated names in CytoTable joins,
  or a compartment prefix (e.g. Image_Metadata_Well vs Metadata_Well) are compared as text.
  Identical columns are reduced to one column with the plain Metadata_ name;
  columns that agree except for missing values are coalesced into the plain name;
  conflicting columns are all kept, so _platemap columns remain only in that case.
- Non-constant metadata columns with identical values but unrelated names are reported only.
  Conflicts, coalesced columns, and identical-value columns are written to a warnings file
  so Nextflow can surface them in the run log.
"""

import hashlib
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


def is_metadata_column(col: str) -> bool:
    return col.startswith("Metadata_") or "_Metadata_" in col


def normalize_metadata_name(col: str, all_columns: set) -> str:
    """
    Normalize a metadata column name for duplicate detection.
    The _platemap suffix added by pycytominer.annotate on name collisions is stripped.
    A numeric suffix (_1, _2, ...) added by DuckDB to repeated names in CytoTable joins
    is stripped only when the base name is also present, so names like Metadata_Site_1 are kept.
    Then any compartment prefix before Metadata_ is removed.
    """
    col = col.removesuffix("_platemap")
    base, _, number = col.rpartition("_")
    if number.isdigit() and base in all_columns:
        col = base
    if not col.startswith("Metadata_"):
        col = "Metadata_" + col.split("_Metadata_", 1)[1]
    return col


def resolve_duplicate_metadata(df: pd.DataFrame) -> tuple[pd.DataFrame, list[str]]:
    """
    Resolve metadata columns that share a normalized name.
    Values are compared as text so that e.g. platemap '24' matches a numeric 24.
    Returns the updated DataFrame and a list of warning messages.
    """
    messages = []
    all_columns = set(df.columns)
    groups = {}
    for col in df.columns:
        if is_metadata_column(col):
            groups.setdefault(normalize_metadata_name(col, all_columns), []).append(col)

    for name, cols in groups.items():
        if len(cols) < 2:
            continue
        # keep the column that already has the normalized name, if any
        keep = name if name in cols else cols[0]
        others = [col for col in cols if col != keep]

        combined = df[keep].astype("string[pyarrow]")
        identical = True
        conflict = False
        for col in others:
            values = df[col].astype("string[pyarrow]")
            both = combined.notna() & values.notna()
            if not (combined[both] == values[both]).all():
                conflict = True
                break
            identical = identical and combined.equals(values)
            combined = combined.combine_first(values)

        if conflict:
            messages.append(
                f"Columns {', '.join(cols)} have conflicting values; all were kept as is."
            )
            continue
        if identical:
            print(
                f"Dropping {', '.join(others)}: identical to {keep}.", file=sys.stderr
            )
        else:
            messages.append(
                f"Columns {', '.join(cols)} differ only in missing values; coalesced into {name}."
            )
            # restore the original type of the kept column where possible
            try:
                df[keep] = combined.astype(df[keep].dtype)
            except (TypeError, ValueError):
                df[keep] = combined
        df = df.drop(columns=others).rename(columns={keep: name})

    return df, messages


def find_identical_metadata(df: pd.DataFrame) -> list[str]:
    """
    Find non-constant metadata columns with identical values but different names.
    Constant columns are skipped, as plate-level values match each other by coincidence.
    Each column is hashed once; columns with matching hashes are confirmed with equals().
    Returns a list of warning messages; no columns are dropped.
    """
    by_hash = {}
    for col in df.columns:
        if not is_metadata_column(col) or df[col].nunique(dropna=False) <= 1:
            continue
        values = df[col].astype("string[pyarrow]")
        row_hashes = pd.util.hash_pandas_object(values, index=False).to_numpy()
        key = hashlib.sha256(row_hashes.tobytes()).hexdigest()
        by_hash.setdefault(key, []).append((col, values))

    messages = []
    for candidates in by_hash.values():
        first_col, first_values = candidates[0]
        matches = [col for col, values in candidates[1:] if first_values.equals(values)]
        if matches:
            messages.append(
                f"Columns {', '.join([first_col] + matches)} have identical values."
            )
    return messages


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
        annotated_df, messages = resolve_duplicate_metadata(annotated_df)
        messages += find_identical_metadata(annotated_df)
        if messages:
            with open(f"{plate_id}_metadata_warnings.txt", "w") as f:
                for message in messages:
                    print(f"WARNING: {message}", file=sys.stderr)
                    f.write(f"{message}\\n")
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
