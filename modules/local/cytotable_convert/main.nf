process CYTOTABLE_CONVERT {
    tag "$meta.plate_id"
    label 'process_medium'

    conda "${moduleDir}/environment.yml"
    container "community.wave.seqera.io/library/pip_cytotable:2c29a8bfc594d5ea"

    input:
    tuple val(meta), path(sqlite)

    output:
    tuple val(meta), path("${meta.plate_id}.parquet"), emit: parquet
    path "versions.yml",                        emit: versions

    script:
    """
    #!/usr/bin/env python3
    import cytotable
    cytotable.convert(
        source_path="${sqlite}",
        dest_path="${meta.plate_id}.parquet",
        dest_datatype="parquet",
        preset="cellprofiler_sqlite_pycytominer",  # formats output for pycytominer compatibility
    )
    # nf-core convention: emit software versions for pipeline provenance tracking
    import sys
    import duckdb
    import pyarrow
    with open("versions.yml", "w") as f:
        f.write('"${task.process}":\\n')
        f.write(f"    python: {sys.version.split()[0]}\\n")
        f.write(f"    cytotable: {cytotable.__version__}\\n")
        f.write(f"    duckdb: {duckdb.__version__}\\n")
        f.write(f"    pyarrow: {pyarrow.__version__}\\n")
    """
}
