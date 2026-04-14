#!/usr/bin/env python3
import os
import sys

import duckdb
import pyarrow

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
)

# nf-core convention: emit core software versions for pipeline provenance tracking


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
