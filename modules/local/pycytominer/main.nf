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

process PYCYTOMINER_NORMALIZE {
    tag "${meta.plate_id} (${meta.aggregate_level})"
    label 'process_low'

    conda "${moduleDir}/environment.yml"
    container {
        workflow.containerEngine == 'singularity' && !task.ext.singularity_pull_docker_container
            ? 'oras://community.wave.seqera.io/library/python_pandas_pyarrow_pip_pycytominer:32e5464c4e0d82cf'
            : 'community.wave.seqera.io/library/python_pandas_pyarrow_pip_pycytominer:e5af62bb1cbba695'
    }

    input:
    tuple val(meta), path(parquet_file), val(normalize_samples), val(normalize_method)

    output:
    tuple val(meta), path("*_normalized.parquet", arity: 1), emit: result
    path "versions.yml", emit: versions, topic: versions

    script:
    output_file = "${meta.plate_id}_${meta.aggregate_level}_normalized.parquet"
    template 'pycytominer_normalize.py'

    stub:
    """
    touch ${meta.plate_id}_${meta.aggregate_level}_normalized.parquet
    cat <<-END_VERSIONS > versions.yml
    "${task.process}":
        stub: stub
    END_VERSIONS
    """
}

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
    val(operation)

    output:
    tuple val(meta), path("*_bulk.parquet", arity: 1), emit: result
    path "versions.yml", emit: versions, topic: versions

    script:
    assert meta_in.aggregate_level == 'cell' : "PYCYTOMINER_AGGREGATE expects aggregate_level='cell', got '${meta_in.aggregate_level}'"
    assert operation in ['median', 'mean'] : "PYCYTOMINER_AGGREGATE: operation must be 'median' or 'mean', got '${operation}'"
    meta = meta_in + [aggregate_level: 'well']
    // platemap columns are included as strata to ensure they are retained in the output metadata
    // all are well-level data, so they won't interfere with aggregation
    def platemap_strata = meta_in.platemap_columns.join(',')
    """
    pycytominer aggregate \\
        --profiles "${parquet_file}" \\
        --output_file "${meta.plate_id}_bulk.parquet" \\
        --strata "Metadata_Plate,Metadata_Well,${platemap_strata}" \\
        --operation "${operation}" \\
        --output_type parquet

    cat <<-END_VERSIONS > versions.yml
    "${task.process}":
        python: \$(python --version | awk '{print \$2}')
        pycytominer: \$(python -c "import importlib.metadata; print(importlib.metadata.version('pycytominer'))")
        pandas: \$(python -c "import pandas; print(pandas.__version__)")
    END_VERSIONS
    """

    stub:
    assert meta_in.aggregate_level == 'cell' : "PYCYTOMINER_AGGREGATE expects aggregate_level='cell', got '${meta_in.aggregate_level}'"
    assert operation in ['median', 'mean'] : "PYCYTOMINER_AGGREGATE: operation must be 'median' or 'mean', got '${operation}'"
    meta = meta_in + [aggregate_level: 'well']
    """
    touch ${meta.plate_id}_bulk.parquet
    cat <<-END_VERSIONS > versions.yml
    "${task.process}":
        stub: stub
    END_VERSIONS
    """
}
