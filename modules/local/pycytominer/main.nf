process PYCYTOMINER_AGGREGATE {
    tag "${meta.plate_id}"
    label 'process_low'

    conda "${moduleDir}/environment.yml"
    container {
        workflow.containerEngine == 'singularity' && !task.ext.singularity_pull_docker_container
            ? 'oras://community.wave.seqera.io/library/python_pandas_pyarrow_pip_pycytominer:32e5464c4e0d82cf'
            : 'community.wave.seqera.io/library/python_pandas_pyarrow_pip_pycytominer:e5af62bb1cbba695'
    }

    input:
    tuple val(meta_in), path(parquet_file)

    output:
    tuple val(meta), path("${meta.plate_id}_bulk.parquet"), emit: result
    path "versions.yml", emit: versions, topic: versions

    script:
    assert meta_in.agg_level == 'cell' : "PYCYTOMINER_AGGREGATE expects agg_level='cell', got '${meta_in.agg_level}'"
    meta = meta_in + [agg_level: 'well']
    template('pycytominer_aggregate.py')

    stub:
    assert meta_in.agg_level == 'cell' : "PYCYTOMINER_AGGREGATE expects agg_level='cell', got '${meta_in.agg_level}'"
    meta = meta_in + [agg_level: 'well']
    """
    touch ${meta.plate_id}_bulk.parquet
    cat <<-END_VERSIONS > versions.yml
    "${task.process}":
        stub: stub
    END_VERSIONS
    """
}
