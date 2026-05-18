process CYTOTABLE_CONVERT {
    tag "${meta.plate_id}"
    label 'process_medium'

    conda "${moduleDir}/environment.yml"
    container {
        workflow.containerEngine == 'singularity' && !task.ext.singularity_pull_docker_container
            ? 'oras://community.wave.seqera.io/library/python_python-duckdb_pyarrow_pip_cytotable:d0acdaa4b0a8a3e2'
            : 'community.wave.seqera.io/library/python_python-duckdb_pyarrow_pip_cytotable:acfb964f6f1660a0'
    }

    input:
    tuple val(meta), path(plate_dir)

    output:
    tuple val(meta), path("${meta.plate_id}.parquet"), emit: parquet
    path "versions.yml", emit: versions, topic: versions

    script:
    template 'cytotable_convert.py'

    stub:
    """
    touch ${meta.plate_id}.parquet
    cat <<-END_VERSIONS > versions.yml
    "${task.process}":
        stub: stub
    END_VERSIONS
    """
}
