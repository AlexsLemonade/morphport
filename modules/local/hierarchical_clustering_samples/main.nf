// Computes hierarchical clustering of the samples within one analysis group.
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
    tuple val(meta), path(embeddings_parquet), path(summary_json)
    val(linkage_method)
    val(distance_metric)
    val(max_samples)

    output:
    tuple val(meta), path("clustering_samples_linkage_${meta.group_tag}.parquet"), path("clustering_samples_leaves_${meta.group_tag}.parquet"), path("clustering_samples_summary_${meta.group_tag}.json"), emit: result
    path "versions.yml", emit: versions, topic: versions

    script:
    meta_json = groovy.json.JsonOutput.toJson(meta)
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
