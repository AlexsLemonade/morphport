process PYCYTOMINER_ANNOTATE {
    tag "${meta.plate_id}"
    label 'process_low'
    label 'publish_optional'

    conda "${moduleDir}/environment.yml"
    container {
        workflow.containerEngine == 'singularity' && !task.ext.singularity_pull_docker_container
            ? 'oras://community.wave.seqera.io/library/python_pandas_pyarrow_pip_pycytominer:7f48ab6b722820f9'
            : 'community.wave.seqera.io/library/python_pandas_pyarrow_pip_pycytominer:db10e902a4043cb1'
    }

    input:
    tuple val(meta), path(parquet_files, arity: '1..*', stageAs: 'input_parquet/part*.parquet'), path(platemap_file)
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
        stub: x.y.z
    END_VERSIONS
    """
}
