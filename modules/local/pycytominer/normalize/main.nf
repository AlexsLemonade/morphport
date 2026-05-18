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
    tuple val(meta_in), path(parquet_file), val(normalize_samples), val(normalize_method)

    output:
    tuple val(meta), path("*_normalized.parquet", arity: 1), emit: result
    path "versions.yml", emit: versions, topic: versions

    script:
    assert normalize_method in ["mad_robustize", "standardize", "robustize", "spherize"]
        : "PYCYTOMINER_NORMALIZE: normalize_method must be one of 'mad_robustize', 'standardize', 'robustize', or 'spherize', got '${normalize_method}'"
    meta = meta_in + [plate_normalized: true, plate_normalization_method: normalize_method, plate_normalization_samples: normalize_samples]
    template 'pycytominer_normalize.py'

    stub:
    assert normalize_method in ["mad_robustize", "standardize", "robustize", "spherize"]
        : "PYCYTOMINER_NORMALIZE: normalize_method must be one of 'mad_robustize', 'standardize', 'robustize', or 'spherize', got '${normalize_method}'"
    meta = meta_in + [plate_normalized: true, plate_normalization_method: normalize_method, plate_normalization_samples: normalize_samples]
    """
    touch ${meta.plate_id}_${meta.aggregate_level}_normalized.parquet
    cat <<-END_VERSIONS > versions.yml
    "${task.process}":
        stub: stub
    END_VERSIONS
    """
}
