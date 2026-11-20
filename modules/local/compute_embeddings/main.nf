process COMPUTE_EMBEDDINGS {
    tag "${meta.group_tag}"
    label 'process_low'

    conda "${moduleDir}/environment.yml"
    container {
        workflow.containerEngine == 'singularity' && !task.ext.singularity_pull_docker_container
            ? 'oras://community.wave.seqera.io/library/python_ca-certificates_polars_numpy_pruned:3c1071edcf64d707'
            : 'community.wave.seqera.io/library/python_ca-certificates_polars_numpy_pruned:00040f01e2f6537f'
    }

    input:
    tuple val(meta), val(plate_metas), path(parquets, stageAs: 'input_parquet/input*.parquet'), path(features_file)
    // Map of named embedding options; see meta.yml for the required keys
    val(options)

    output:
    tuple val(meta), path("embeddings_${meta.group_tag}.parquet"), path("embeddings_summary_${meta.group_tag}.json"), emit: result
    path "versions.yml", emit: versions, topic: versions

    script:
    // Every option must be given; other keys are ignored
    def option_keys = ['pca_components', 'umap_neighbors', 'umap_min_dist', 'seed', 'max_nan_fraction', 'min_rows']
    def missing_keys = option_keys - options.keySet()
    assert !missing_keys
        : "COMPUTE_EMBEDDINGS: options is missing keys ${missing_keys}"
    meta_json = groovy.json.JsonOutput.toJson(meta)
    options_json = groovy.json.JsonOutput.toJson(options)
    template 'compute_embeddings.py'

    stub:
    """
    touch embeddings_${meta.group_tag}.parquet
    cat <<-END_JSON > embeddings_summary_${meta.group_tag}.json
    {
        "aggregate_level": "${meta.aggregate_level}",
        "stub": true
    }
    END_JSON
    cat <<-END_VERSIONS > versions.yml
    "${task.process}":
        stub: x.y.z
    END_VERSIONS
    """
}
