#!/usr/bin/env python3
"""
Nextflow template script to write Hive-style partitioned parquet output under {plate_id}/.

Platemap metadata has already been joined in the PYCYTOMINER_ANNOTATE step, which
ensures all partition columns are present and non-null.
"""

import json
import sys
from importlib.metadata import version
from pathlib import Path
from urllib.parse import unquote

import duckdb


def resolve_partition_columns(partition_columns_str: str) -> list[str]:
    """Parse comma-separated column names → Metadata_-prefixed list."""
    raw = [c.strip() for c in partition_columns_str.split(",") if c.strip()]
    return [c if c.startswith("Metadata_") else f"Metadata_{c}" for c in raw]


def partition_manifest(output_dir: Path) -> list[dict]:
    """
    List each Hive partition directory under output_dir (relative to it) with its exact
    partition values. DuckDB percent-encodes values in directory names (e.g. 'drug A' ->
    'drug%20A', including '%' itself), so the values are decoded with unquote.
    """
    partition_dirs = sorted(
        {path.parent.relative_to(output_dir) for path in output_dir.rglob("*.parquet")}
    )
    return [
        {
            "path": str(partition_dir),
            "values": {
                col: unquote(value)
                for col, value in (part.split("=", 1) for part in partition_dir.parts)
            },
        }
        for partition_dir in partition_dirs
    ]


def write_versions(task_process: str):
    with open("versions.yml", "w") as f:
        f.write(f'"{task_process}":\\n')
        f.write(f"    python: {sys.version.split()[0]}\\n")
        f.write(f"    duckdb: {version('duckdb')}\\n")


def main():
    # capture input variables from Nextflow
    plate_parquet_file = "${parquet_file}"
    plate_id = "${meta.plate_id}"
    partition_columns_str = "${partition_columns_str}"
    n_threads = int("${task.cpus}")
    task_process = "${task.process}"

    duckdb.execute(f"SET threads = {n_threads}")

    partition_cols = resolve_partition_columns(partition_columns_str)

    plate_db = duckdb.read_parquet(plate_parquet_file)

    plate_db.write_parquet(
        plate_id,
        partition_by=partition_cols,
        filename_pattern=plate_id + "_{i}",
    )

    # record each partition directory with its exact values for Nextflow to read
    manifest = partition_manifest(Path(plate_id))
    with open(f"partitions_{plate_id}.json", "w") as f:
        json.dump(manifest, f, indent=2)

    partition_sizes = {}
    for col in partition_cols:
        counts = plate_db.aggregate(f"{col}, count(*) as count", col).to_df()
        partition_sizes[col] = dict(zip(counts[col], counts["count"]))

    summary = {
        "plate_id": plate_id,
        "partition_columns": partition_cols,
        "partitions": partition_sizes,
    }
    with open(f"platemap_summary_{plate_id}.json", "w") as f:
        json.dump(summary, f, indent=2)

    write_versions(task_process)


if __name__ == "__main__":
    main()
