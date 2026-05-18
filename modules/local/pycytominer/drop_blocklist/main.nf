process PYCYTOMINER_DROP_BLOCKLIST {
    tag "${meta.plate_id}"
    label 'process_low'

    conda "${moduleDir}/environment.yml"
    container {
        workflow.containerEngine == 'singularity' && !task.ext.singularity_pull_docker_container
            ? 'oras://community.wave.seqera.io/library/python_pandas_pyarrow_pip_pycytominer:7f48ab6b722820f9'
            : 'community.wave.seqera.io/library/python_pandas_pyarrow_pip_pycytominer:db10e902a4043cb1'
    }

    input:
    tuple val(meta), path(parquet_file)

    output:
    tuple val(meta), path("*_blocklist_filtered.parquet", arity: 1), emit: result
    path "versions.yml", emit: versions, topic: versions

    script:
    template 'pycytominer_drop_blocklist.py'

    stub:
    """
    touch ${meta.plate_id}_blocklist_filtered.parquet
    cat <<-END_VERSIONS > versions.yml
    "${task.process}":
        stub: stub
    END_VERSIONS
    """
}
