#!/usr/bin/env python3
"""
Nextflow template script to convert a CellProfiler SQLite database to Parquet format using the cytotable library.
It is designed to be run as part of a Nextflow workflow, with parameters passed in from the workflow.
"""

import os
import sys
import tempfile
from importlib.metadata import version

import pyarrow
from parsl import Config
from parsl.executors import HighThroughputExecutor


def write_versions(task_process: str):
    with open("versions.yml", "w") as f:
        f.write(f'"{task_process}":\\n')
        f.write(f"    python: {sys.version.split()[0]}\\n")
        f.write(f"    cytotable: {version('cytotable')}\\n")
        f.write(f"    duckdb: {version('duckdb')}\\n")
        f.write(f"    pyarrow: {version('pyarrow')}\\n")


def main():
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

    # containers can be run as a numeric uid with no /etc/passwd entry
    # (e.g. via a docker run option mapping to the host uid/gid), which makes
    # getpass.getuser() fail and crashes parsl's DataFlowKernel init
    os.environ.setdefault("USER", "nextflow")
    # duckdb uses HOME for its config/extensions directory; the container
    # image sets HOME to "/", which isn't writable by an unmapped uid, so
    # fall back to a writable directory in that case
    if not os.access(os.environ.get("HOME", "/"), os.W_OK):
        os.environ["HOME"] = tempfile.gettempdir()

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

    write_versions(task_process)


if __name__ == "__main__":
    main()
