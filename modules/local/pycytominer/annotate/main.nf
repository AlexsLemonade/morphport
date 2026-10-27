process PYCYTOMINER_ANNOTATE {
    tag "${meta.plate_id}"
    label 'process_low'
    label 'publish_optional'

    conda "${moduleDir}/environment.yml"
    container {
        workflow.containerEngine == 'singularity' && !task.ext.singularity_pull_docker_container
            ? 'oras://community.wave.seqera.io/library/python_ca-certificates_pandas_pyarrow_pruned:77fbd4b29f56c6c0'
            : 'community.wave.seqera.io/library/python_ca-certificates_pandas_pyarrow_pruned:d60b17a9b7cc60c9'
    }

    input:
    tuple val(meta), path(parquet_files, arity: '1..*', stageAs: 'input_parquet/part*.parquet'), path(platemap_file)
    val(platemap_well_column)

    output:
    tuple val(meta), path("*_annotated.parquet", arity: 1), emit: result
    tuple val(task.process), val(meta.plate_id), path("*_metadata_warnings.txt"), emit: warnings, optional: true, topic: warnings
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
