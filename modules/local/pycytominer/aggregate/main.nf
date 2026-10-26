process PYCYTOMINER_AGGREGATE {
    tag "${meta.plate_id}"
    label 'process_low'

    conda "${moduleDir}/environment.yml"
    container {
        workflow.containerEngine == 'singularity' && !task.ext.singularity_pull_docker_container
            ? 'oras://community.wave.seqera.io/library/python_ca-certificates_pandas_pyarrow_pruned:77fbd4b29f56c6c0'
            : 'community.wave.seqera.io/library/python_ca-certificates_pandas_pyarrow_pruned:d60b17a9b7cc60c9'
    }

    input:
    tuple val(meta_in), path(parquet_file)
    val(operation)

    output:
    tuple val(meta), path("well_*.parquet", arity: 1), emit: result
    path "versions.yml", emit: versions, topic: versions

    script:
    assert meta_in.aggregate_level == 'cell' : "PYCYTOMINER_AGGREGATE expects aggregate_level='cell', got '${meta_in.aggregate_level}'"
    assert operation in ['median', 'mean'] : "PYCYTOMINER_AGGREGATE: operation must be 'median' or 'mean', got '${operation}'"
    meta = meta_in + [aggregate_level: 'well']
    template 'pycytominer_aggregate.py'

    stub:
    assert meta_in.aggregate_level == 'cell' : "PYCYTOMINER_AGGREGATE expects aggregate_level='cell', got '${meta_in.aggregate_level}'"
    assert operation in ['median', 'mean'] : "PYCYTOMINER_AGGREGATE: operation must be 'median' or 'mean', got '${operation}'"
    meta = meta_in + [aggregate_level: 'well']
    """
    touch well_${meta.plate_id}.parquet
    cat <<-END_VERSIONS > versions.yml
    "${task.process}":
        stub: x.y.z
    END_VERSIONS
    """
}
