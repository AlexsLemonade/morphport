#!/usr/bin/env python3
"""
This script template converts a CellProfiler output directory to Parquet format.
For most presets it uses the cytotable library; for the cellprofiler_csv_collated
preset it uses DuckDB to convert a single pre-collated CSV directly to Parquet.
It is designed to be run as part of a Nextflow workflow, with parameters passed in from the workflow.
"""

import os
import sys
from pathlib import Path

import duckdb
import pyarrow
import pyarrow.csv
import pyarrow.parquet
from parsl import Config
from parsl.executors import HighThroughputExecutor


def convert_csv_collated(source_dir: str, dest_path: str, threads: int) -> None:
    """Convert a single pre-collated CellProfiler CSV to Parquet using DuckDB."""
    csv_file = next(Path(source_dir).glob("*.csv"))
    conn = duckdb.connect(config={"threads": threads})
    conn.execute(f"""
        COPY (SELECT * FROM read_csv('{csv_file}'))
        TO '{dest_path}'
        (FORMAT PARQUET)
    """)


preset = "${meta.cytotable_preset}"

if preset == "cellprofiler_csv_collated":
    # For the cellprofiler_csv_collated preset, we have a single CSV file
    # that can be converted directly with PyArrow
    threads = int("${task.cpus}")
    convert_csv_collated("${plate_dir}", "${meta.plate_id}.parquet", threads)
else:
    # Set multiprocessing and thread allocation
    # Redundant int("${}") conversion to fool python linters/formatters
    parsl_workers = int("${task.cpus}")
    # allow 2 duckdb and pyarrow threads per worker
    # this seems to work reasonably well to match CPU usage to the allocated cpus, at least in local testing
    subprocess_threads = 2
    pyarrow.set_cpu_count(subprocess_threads)
    pyarrow.set_io_thread_count(subprocess_threads)
    os.environ["CYTOTABLE_MAX_THREADS"] = str(subprocess_threads)

    # import after environment variable is set to ensure cytotable picks up the correct thread count
    import cytotable  # noqa: E402

    cytotable.convert(
        source_path="${plate_dir}",
        dest_path="${meta.plate_id}.parquet",
        dest_datatype="parquet",
        preset=preset,
        chunksize=100_000,
        parsl_config=Config(
            executors=[HighThroughputExecutor(max_workers_per_node=parsl_workers)]
        ),
    )

# for nf-core software version tracking
with open("versions.yml", "w") as f:
    cytotable_version = (
        cytotable.__version__ if preset != "cellprofiler_csv_collated" else "N/A"
    )
    f.write(
        "\\n".join(
            [
                '"${task.process}":',
                f"    python: {sys.version.split()[0]}",
                f"    cytotable: {cytotable_version}",
                f"    duckdb: {duckdb.__version__}",
                f"    pyarrow: {pyarrow.__version__}",
            ]
        )
        + "\\n"
    )
