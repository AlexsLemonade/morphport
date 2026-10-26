process CYTOTABLE_CONVERT {
    tag "${meta.plate_id}"
    label 'process_medium'

    conda "${moduleDir}/environment.yml"
    container {
        workflow.containerEngine == 'singularity' && !task.ext.singularity_pull_docker_container
            ? 'oras://community.wave.seqera.io/library/python_ca-certificates_python-duckdb_pyarrow_pruned:2c18f088c6c0c6ad'
            : 'community.wave.seqera.io/library/python_ca-certificates_python-duckdb_pyarrow_pruned:db49352681a8da84'
    }

    input:
    tuple val(meta), path(sqlite_file)

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
        stub: x.y.z
    END_VERSIONS
    """
}
