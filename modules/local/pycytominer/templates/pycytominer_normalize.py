#!/usr/bin/env python3

import importlib.metadata
import sys

import pandas as pd
import pycytominer


def write_versions(task_process):
    with open("versions.yml", "w") as f:
        f.write(f'"{task_process}":\\n')
        f.write(f"    python: {sys.version.split()[0]}\\n")
        f.write(f"    pycytominer: {importlib.metadata.version('pycytominer')}\\n")
        f.write(f"    pandas: {pd.__version__}\\n")


def main():
    # capture input variables from Nextflow
    parquet_file = "${parquet_file}"
    output_file = "${output_file}"
    samples = "${normalize_samples}"
    method = "${normalize_method}"
    task_process = "${task.process}"

    pycytominer.normalize(
        profiles=parquet_file,
        features="infer",
        meta_features="infer",
        samples=samples,
        method=method,
        output_file=output_file,
        output_type="parquet",
    )

    write_versions(task_process)


if __name__ == "__main__":
    main()
