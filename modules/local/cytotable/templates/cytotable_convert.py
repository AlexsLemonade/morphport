#!/usr/bin/env python3
"""
Nextflow template script to convert a CellProfiler SQLite database to Parquet format using the cytotable library.
It is designed to be run as part of a Nextflow workflow, with parameters passed in from the workflow.
"""

import os
import sys
import tempfile

import duckdb
import pyarrow
from parsl import Config
from parsl.executors import HighThroughputExecutor

# capture input variables from the Nextflow workflow
plate_dir = "${plate_dir}"
plate_id = "${meta.plate_id}"
cytotable_preset = "${meta.cytotable_preset}"
task_cpus = int("${task.cpus}")
task_process = "${task.process}"

# Set multiprocessing and thread allocation

parsl_workers = task_cpus
# allow 2 duckdb and pyarrow threads per worker
# this seems to work reasonably well to match CPU usage to the allocated cpus, at least in local testing
subprocess_threads = 2
pyarrow.set_cpu_count(subprocess_threads)
pyarrow.set_io_thread_count(subprocess_threads)
os.environ["CYTOTABLE_MAX_THREADS"] = str(subprocess_threads)

# import after environment variable is set to ensure cytotable picks up the correct thread count
import cytotable  # noqa: E402

with tempfile.TemporaryDirectory() as parsl_run_dir:
    cytotable.convert(
        source_path=plate_dir,
        dest_path=f"{plate_id}.parquet",
        dest_datatype="parquet",
        preset=cytotable_preset,
        chunksize=100_000,
        parsl_config=Config(
            executors=[HighThroughputExecutor(max_workers_per_node=parsl_workers)],
            run_dir=parsl_run_dir,
        ),
    )

# for nf-core software version tracking
with open("versions.yml", "w") as f:
    f.write(
        "\\n".join(
            [
                f'"{task_process}":',
                f"    python: {sys.version.split()[0]}",
                f"    cytotable: {cytotable.__version__}",
                f"    duckdb: {duckdb.__version__}",
                f"    pyarrow: {pyarrow.__version__}",
            ]
        )
        + "\\n"
    )
