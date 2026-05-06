#!/usr/bin/env python3
"""
Nextflow template script to write Hive-style partitioned parquet output under {plate_id}/.

Platemap metadata has already been joined in the PYCYTOMINER_ANNOTATE step.
Partition columns with null values are filled with '_NA_'; any partition column
absent from the data entirely is filled with '_Absent_' so every row receives a
complete set of Hive path segments.
"""

import json
import sys

import duckdb


def resolve_partition_columns(partition_columns_str: str) -> list[str]:
    """Parse comma-separated column names → Metadata_-prefixed list."""
    raw = [c.strip() for c in partition_columns_str.split(",") if c.strip()]
    return [c if c.startswith("Metadata_") else f"Metadata_{c}" for c in raw]


def write_versions(task_process: str):
    with open("versions.yml", "w") as f:
        f.write(
            "\\n".join(
                [
                    f'"{task_process}":',
                    f"    python: {sys.version.split()[0]}",
                    f"    duckdb: {duckdb.__version__}",
                ]
            )
            + "\\n"
        )


def main():
    # capture input variables from Nextflow
    plate_parquet_file = "${parquet_file}"
    plate_id = "${meta.plate_id}"
    partition_columns_str = "${platemap_condition_columns}"
    n_threads = int("${task.cpus}")
    task_process = "${task.process}"

    duckdb.execute(f"SET threads = {n_threads}")

    partition_cols = resolve_partition_columns(partition_columns_str)

    plate_db = duckdb.read_parquet(plate_parquet_file)

    existing = [c for c in partition_cols if c in plate_db.columns]
    absent = [c for c in partition_cols if c not in plate_db.columns]

    # build a select expression to fill missing partition columns with '_NA_' or '_Absent_'
    # This will ultimately have the form:
    # select * replace(coalesce(C1, '_NA_') AS C1, coalesce(C2, '_NA_') AS C2), '_Absent_' AS NewC1, '_Absent_' AS NewC2
    replace_exprs = [f"coalesce({c}, '_NA_') AS {c}" for c in existing]
    absent_exprs = [f"'_Absent_' AS {c}" for c in absent]

    star = f"* replace ({', '.join(replace_exprs)})" if replace_exprs else "*"
    plate_db = plate_db.select(", ".join([star] + absent_exprs))

    plate_db.write_parquet(
        plate_id,
        partition_by=partition_cols,
        filename_pattern=plate_id + "_{i}",
    )

    partition_sizes = {}
    for col in partition_cols:
        counts = plate_db.aggregate(f"{col}, count(*) as count", col).to_df()
        partition_sizes[col] = dict(zip(counts[col], counts["count"]))

    summary = {
        "plate_id": plate_id,
        "partition_columns": partition_cols,
        "partitions": partition_sizes,
    }
    with open(f"{plate_id}_platemap_summary.json", "w") as f:
        json.dump(summary, f, indent=2)

    write_versions(task_process)


if __name__ == "__main__":
    main()
