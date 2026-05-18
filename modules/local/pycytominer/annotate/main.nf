process PYCYTOMINER_ANNOTATE {
    tag "${meta.plate_id}"
    label 'process_low'
    label 'publish_optional'

    conda "${moduleDir}/environment.yml"
    container {
        workflow.containerEngine == 'singularity' && !task.ext.singularity_pull_docker_container
            ? 'oras://community.wave.seqera.io/library/python_pandas_pyarrow_pip_pycytominer:32e5464c4e0d82cf'
            : 'community.wave.seqera.io/library/python_pandas_pyarrow_pip_pycytominer:e5af62bb1cbba695'
    }

    input:
    tuple val(meta), path(parquet_file), path(platemap_file)
    val(platemap_well_column)

    output:
    tuple val(meta), path("*_annotated.parquet", arity: 1), emit: result
    path "versions.yml", emit: versions, topic: versions

    script:
    template 'pycytominer_annotate.py'

    stub:
    """
    touch ${meta.plate_id}_annotated.parquet
    cat <<-END_VERSIONS > versions.yml
    "${task.process}":
        stub: stub
    END_VERSIONS
    """
}
