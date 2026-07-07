// Computes hierarchical clustering of the samples within one analysis group.
process HIERARCHICAL_CLUSTERING {
    tag "${group_tag}"
    label 'process_medium'

    conda "${moduleDir}/environment.yml"
    container {
        // TODO: replace with a Wave container built from this module's environment.yml
        // (needs network access to build; see modules/local/compute_embeddings/main.nf
        // for the pattern used elsewhere in this repo).
        workflow.containerEngine == 'singularity' && !task.ext.singularity_pull_docker_container
            ? 'oras://community.wave.seqera.io/library/python_polars_numpy_scipy:TODO'
            : 'community.wave.seqera.io/library/python_polars_numpy_scipy:TODO'
    }

    input:
    tuple val(meta), path(embeddings_parquet), path(summary_json)
    val(linkage_method)
    val(distance_metric)
    val(max_samples)

    output:
    tuple val(meta), path("clustering_${group_tag}.json"), emit: result
    path "versions.yml", emit: versions, topic: versions

    script:
    group_tag = groupTag(meta)
    // Bundle group identity for the template as one JSON blob, matching the
    // convention used by compute_embeddings.
    grouping_info = groovy.json.JsonOutput.toJson([
        grouping_values: (meta.grouping_columns ?: []).collectEntries { col -> [(col): meta[col]] },
        plate_ids      : meta.plate_ids,
    ])
    template 'hierarchical_clustering.py'

    stub:
    group_tag = groupTag(meta)
    """
    cat <<-END_JSON > clustering_${group_tag}.json
    {
        "group_tag": "${group_tag}",
        "aggregate_level": "${meta.aggregate_level}",
        "stub": true
    }
    END_JSON
    cat <<-END_VERSIONS > versions.yml
    "${task.process}":
        stub: stub
    END_VERSIONS
    """
}

// Build a filesystem-safe tag from meta:
//   <aggregate_level>__<col1>=<val1>__<col2>=<val2>
// When there are no grouping columns, falls back to "<aggregate_level>__all".
def groupTag(meta) {
    def cols = meta.grouping_columns ?: []
    def parts = cols ? cols.collect { col -> "${col.replaceFirst('^Metadata_', '')}=${meta[col]}" }.join('__') : 'all'
    "${meta.aggregate_level}__${parts}".replaceAll('[^A-Za-z0-9._=-]', '_')
}
