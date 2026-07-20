process PYCYTOMINER_NORMALIZE {
    tag "${meta.plate_id} (${meta.aggregate_level})"
    label 'process_low'

    conda "${moduleDir}/environment.yml"
    container {
        workflow.containerEngine == 'singularity' && !task.ext.singularity_pull_docker_container
            ? 'oras://community.wave.seqera.io/library/python_pandas_pyarrow_pip_pycytominer:7f48ab6b722820f9'
            : 'community.wave.seqera.io/library/python_pandas_pyarrow_pip_pycytominer:db10e902a4043cb1'
    }

    input:
    tuple val(meta_in), path(parquet_file), val(normalize_samples), val(normalize_method)

    output:
    tuple val(meta), path("normalized_*.parquet", arity: 1), emit: result
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
    touch normalized_${meta.aggregate_level}_${meta.plate_id}.parquet
    cat <<-END_VERSIONS > versions.yml
    "${task.process}":
        stub: stub
    END_VERSIONS
    """
}
