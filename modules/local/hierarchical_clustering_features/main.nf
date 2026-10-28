// Computes hierarchical clustering of the selected features within one analysis group.
process HIERARCHICAL_CLUSTERING_FEATURES {
    tag "${meta.group_tag}"
    label 'process_low'

    conda "${moduleDir}/environment.yml"
    container {
        workflow.containerEngine == 'singularity' && !task.ext.singularity_pull_docker_container
            ? 'oras://community.wave.seqera.io/library/python_ca-certificates_polars_numpy_scipy:8dd70f24b9f42bab'
            : 'community.wave.seqera.io/library/python_ca-certificates_polars_numpy_scipy:bd4e20565a2da4a1'
    }

    input:
    tuple val(meta), val(_plate_metas), path(parquets, stageAs: 'input_parquet/input*.parquet'), path(features_file)
    val(linkage_method)
    val(distance_metric)
    val(max_nan_fraction)

    output:
    tuple val(meta),
        path("clustering_features_linkage_${meta.group_tag}.parquet"),
        path("clustering_features_leaves_${meta.group_tag}.parquet"),
        path("clustering_features_summary_${meta.group_tag}.json"),
        emit: result
    path "versions.yml", emit: versions, topic: versions

    script:
    meta_json = groovy.json.JsonOutput.toJson(meta)
    template 'hierarchical_clustering_features.py'

    stub:
    """
    touch clustering_features_linkage_${meta.group_tag}.parquet
    touch clustering_features_leaves_${meta.group_tag}.parquet
    cat <<-END_JSON > clustering_features_summary_${meta.group_tag}.json
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
