process COMPUTE_EMBEDDINGS {
    tag "${prefix}"
    label 'process_medium'

    conda "${moduleDir}/environment.yml"
    // Container directive omitted; add Wave-generated container URLs here once the
    // environment is locked. Pipeline currently runs via the conda or wave profile.

    input:
    // stageAs renames each parquet to input_0.parquet, input_1.parquet, ...
    // This is to avoid collisions for multiple files from the same plate
    tuple val(meta), val(plate_metas), path(parquets, stageAs: 'input_*.parquet')
    val(pca_components)
    val(umap_neighbors)
    val(umap_min_dist)
    val(random_seed)
    val(max_nan_fraction)
    val(min_rows)

    output:
    tuple val(meta), path("${prefix}_embeddings.parquet"), path("${prefix}_summary.json"), emit: result
    path "versions.yml", emit: versions, topic: versions

    script:
    prefix = groupTag(meta)
    // Bundle group identity for the template as one JSON blob. Lets the Python side
    // parse a single value rather than several stringly-typed bindings.
    grouping_info = groovy.json.JsonOutput.toJson([
        grouping_values: (meta.grouping_columns ?: []).collectEntries { col -> [(col): meta[col]] },
        plate_ids      : meta.plate_ids,
    ])
    template 'compute_embeddings.py'

    stub:
    prefix = groupTag(meta)
    """
    touch ${prefix}_embeddings.parquet
    cat <<-END_JSON > ${prefix}_summary.json
    {
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
    def parts = cols ? cols.collect { col -> "${col}=${meta[col]}" }.join('__') : 'all'
    "${meta.aggregate_level}__${parts}".replaceAll('[^A-Za-z0-9._=-]', '_')
}
