// Computes hierarchical clustering of the samples within one analysis group,
// using either the feature-selected profiles or the PCA embeddings.
process HIERARCHICAL_CLUSTERING_SAMPLES {
    tag "${meta.group_tag}"
    label 'process_single'

    conda "${moduleDir}/environment.yml"
    container {
        workflow.containerEngine == 'singularity' && !task.ext.singularity_pull_docker_container
            ? 'oras://community.wave.seqera.io/library/python_ca-certificates_polars_numpy_scipy:8dd70f24b9f42bab'
            : 'community.wave.seqera.io/library/python_ca-certificates_polars_numpy_scipy:bd4e20565a2da4a1'
    }

    input:
    // features_file is the selected feature list when clustering the feature-selected profiles,
    // or empty ([]) when clustering the PCs of an embeddings parquet;
    // embeddings_summary_json is the compute_embeddings summary when clustering PCs, or empty ([])
    tuple val(meta), path(parquets, stageAs: 'input_parquet/input*.parquet'), path(features_file), path(embeddings_summary_json)
    val(distance_metric)
    val(max_nan_fraction)
    val(max_samples)

    output:
    tuple val(meta), path("clustering_samples_linkage_${meta.group_tag}.parquet"), path("clustering_samples_leaves_${meta.group_tag}.parquet"), path("clustering_samples_summary_${meta.group_tag}.json"), emit: result
    path "versions.yml", emit: versions, topic: versions

    script:
    meta_json = groovy.json.JsonOutput.toJson(meta)
    // optional inputs are empty lists when not provided
    features_file_name = features_file ?: ''
    embeddings_summary_name = embeddings_summary_json ?: ''
    template 'hierarchical_clustering_samples.py'

    stub:
    """
    touch clustering_samples_linkage_${meta.group_tag}.parquet
    touch clustering_samples_leaves_${meta.group_tag}.parquet
    cat <<-END_JSON > clustering_samples_summary_${meta.group_tag}.json
    {
        "group_tag": "${meta.group_tag}",
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
