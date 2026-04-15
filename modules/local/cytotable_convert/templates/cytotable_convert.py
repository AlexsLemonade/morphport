#!/usr/bin/env python3
"""
This script template converts a CellProfiler SQLite database to Parquet format using the cytotable library.
It is designed to be run as part of a Nextflow workflow, with parameters passed in from the workflow.
"""

import os
import sys

import duckdb
import pyarrow
from parsl import Config
from parsl.executors import ThreadPoolExecutor

# Limit threads used by DuckDB instances
# Must be set before importing cytotable
os.environ["CYTOTABLE_MAX_THREADS"] = "${task.cpus}"

import cytotable  # noqa: E402

cytotable.convert(
    source_path="${sqlite}",
    dest_path="${meta.plate_id}.parquet",
    dest_datatype="parquet",
    preset="${meta.cytotable_preset}",
    chunksize=100_000,  # Tune this based on memory constraints and performance testing
    parsl_config=Config(
        executors=[ThreadPoolExecutor(max_threads=int("${task.cpus}"))]
    ),
)

# for nf-core software version tracking
with open("versions.yml", "w") as f:
    f.write(
        "\\n".join(
            [
                '"${task.process}":',
                f"    python: {sys.version.split()[0]}",
                f"    cytotable: {cytotable.__version__}",
                f"    duckdb: {duckdb.__version__}",
                f"    pyarrow: {pyarrow.__version__}",
            ]
        )
        + "\\n"
    )
