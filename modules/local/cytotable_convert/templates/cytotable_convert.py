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
from parsl.executors import HighThroughputExecutor

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
    preset="${meta.cytotable_preset}",
    chunksize=100_000,
    parsl_config=Config(
        executors=[HighThroughputExecutor(max_workers_per_node=parsl_workers)]
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
