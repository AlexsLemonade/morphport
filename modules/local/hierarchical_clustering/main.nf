include { groupTag; metaJson } from '../../../lib/morphport_utils.nf'

// Computes hierarchical clustering of the samples within one analysis group.
process HIERARCHICAL_CLUSTERING {
    tag "${group_tag}"
    label 'process_single'

    conda "${moduleDir}/environment.yml"
    container {
        workflow.containerEngine == 'singularity' && !task.ext.singularity_pull_docker_container
            ? 'oras://community.wave.seqera.io/library/python_polars_numpy_scipy:64a657f6279a8116'
            : 'community.wave.seqera.io/library/python_polars_numpy_scipy:8ff74785ccdd6b8d'
    }

    input:
    tuple val(meta), path(embeddings_parquet), path(summary_json)
    val(linkage_method)
    val(distance_metric)
    val(max_samples)

    output:
    tuple val(meta), path("clustering_linkage_${group_tag}.parquet"), path("clustering_leaves_${group_tag}.parquet"), path("clustering_summary_${group_tag}.json"), emit: result
    path "versions.yml", emit: versions, topic: versions

    script:
    group_tag = groupTag(meta)
    meta_json = metaJson(meta)
    template 'hierarchical_clustering.py'

    stub:
    group_tag = groupTag(meta)
    """
    touch clustering_linkage_${group_tag}.parquet
    touch clustering_leaves_${group_tag}.parquet
    cat <<-END_JSON > clustering_summary_${group_tag}.json
    {
        "group_tag": "${group_tag}",
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
