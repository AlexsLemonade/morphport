#!/usr/bin/env python3
"""
Joins platemap metadata into a per-plate parquet file and writes
Hive-style partitioned parquet output under {plate_id}/.

All platemap columns (except plate_map_name) are added to the parquet as
Metadata_-prefixed columns. Any partition column absent from the platemap is
filled with 'NA' so every row receives a complete set of Hive path segments.
Wells with no platemap entry receive NULL for all platemap columns (and 'NA'
for partition columns).
"""

import json
import sys

import duckdb
import pandas as pd


def resolve_partition_columns(partition_columns_str):
    """Parse comma-separated column names → Metadata_-prefixed list."""
    raw = [c.strip() for c in partition_columns_str.split(",") if c.strip()]
    return [c if c.startswith("Metadata_") else f"Metadata_{c}" for c in raw]


def load_platemap(platemap_file, partition_cols):
    """
    Load the platemap TSV and return a processed DataFrame.
    - Renames well_position → Metadata_Well
    - Drops plate_map_name
    - Adds Metadata_ prefix to all other columns
    - Adds any partition columns absent from the platemap as constant 'NA'
      (column not applicable to this platemap format)
    - NULL values in existing partition columns are left as-is; DuckDB will write
      those partitions as __HIVE_DEFAULT_PARTITION__ (wells with no platemap entry)
    """
    df = pd.read_csv(platemap_file, sep="\\t", encoding="utf-8-sig")
    df = df.rename(columns={"well_position": "Metadata_Well"})
    df = df.drop(columns=["plate_map_name"], errors="ignore")
    df = df.rename(
        columns=lambda c: c if c.startswith("Metadata_") else f"Metadata_{c}"
    )
    # fill in any missing partition cols with "NA" for consistency in partition paths
    for col in partition_cols:
        if col not in df.columns:
            df[col] = "NA"
    return duckdb.from_df(df)


def write_versions(task_process):
    with open("versions.yml", "w") as f:
        f.write(
            "\\n".join(
                [
                    f'"{task_process}":',
                    f"    python: {sys.version.split()[0]}",
                    f"    duckdb: {duckdb.__version__}",
                    f"    pandas: {pd.__version__}",
                ]
            )
            + "\\n"
        )


def main():
    plate_parquet_file = "${parquet_file}"
    platemap_file = "${platemap_file}"
    plate_id = "${meta.plate_id}"
    partition_columns_str = "${partition_columns}"

    partition_cols = resolve_partition_columns(partition_columns_str)

    platemap_df = load_platemap(platemap_file, partition_cols)
    plate_db = duckdb.read_parquet(plate_parquet_file)

    joined_db = plate_db.join(platemap_df, condition="Metadata_Well", how="left")

    # check that the join worked by confirming we have the same number of rows as the original parquet
    if len(plate_db) != len(joined_db):
        raise ValueError(
            "Joined database has different number of rows than original parquet,"
            " there may be an error with the platemap join."
            f" Original parquet rows: {len(plate_db)}, joined database rows: {len(joined_db)}"
        )

    joined_db.write_parquet(
        plate_id,
        partition_by=partition_cols,
        filename_pattern=f"{plate_id}_{{i}}",
    )

    partition_sizes = {}
    for col in partition_cols:
        counts = joined_db.aggregate(f"{col}, count(*) as count", col).to_df()
        print(counts)
        partition_sizes[col] = dict(zip(counts[col], counts["count"]))

    summary = {
        "plate_id": plate_id,
        "platemap_file": platemap_file,
        "partition_columns": partition_cols,
        "partitions": partition_sizes,
    }
    with open(f"{plate_id}_platemap_summary.json", "w") as f:
        json.dump(summary, f, indent=2)

    write_versions("${task.process}")


if __name__ == "__main__":
    main()
