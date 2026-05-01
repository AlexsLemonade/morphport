process PYCYTOMINER_AGGREGATE {
    tag "${meta.plate_id}"
    label 'process_low'

    conda "${moduleDir}/environment.yml"
    container {
        workflow.containerEngine == 'singularity' && !task.ext.singularity_pull_docker_container
            ? 'TBD'
            : 'TBD'
    }

    input:
    tuple val(meta), path(parquet_file)

    output:
    tuple val(meta + [agg_level: 'well']), path("${meta.plate_id}_bulk.parquet"), emit: result
    path "versions.yml", emit: versions, topic: versions

    script:
    assert meta.agg_level == 'cell' : "PYCYTOMINER_AGGREGATE expects agg_level='cell', got '${meta.agg_level}'"
    template('pycytominer_aggregate.py')

    stub:
    """
    touch ${meta.plate_id}_bulk.parquet
    cat <<-END_VERSIONS > versions.yml
    "${task.process}":
        stub: stub
    END_VERSIONS
    """
}
