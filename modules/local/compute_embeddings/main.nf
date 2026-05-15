process COMPUTE_EMBEDDINGS {
    tag "${prefix}"
    label 'process_medium'

    conda "${moduleDir}/environment.yml"
    // Container directive omitted; add Wave-generated container URLs here once the
    // environment is locked. Pipeline currently runs via the conda or wave profile.

    input:
    tuple val(group_meta), val(plate_metas), path(parquets)
    val(n_components)
    val(n_neighbors)
    val(min_dist)
    val(random_seed)

    output:
    tuple val(group_meta), path("${prefix}_embeddings.parquet"), path("${prefix}_summary.json"), emit: result
    path "versions.yml", emit: versions, topic: versions

    script:
    prefix      = groupTag(group_meta)
    plate_ids   = plate_metas.collect { it.plate_id }.join(',')
    grouping_cols_str   = (group_meta.grouping_columns ?: []).join(',')
    grouping_values_str = (group_meta.grouping_columns ?: [])
        .collect { col -> "${col}=${group_meta[col]}" }
        .join(',')
    template 'compute_embeddings.py'

    stub:
    prefix = groupTag(group_meta)
    """
    touch ${prefix}_embeddings.parquet
    cat <<-END_JSON > ${prefix}_summary.json
    {
        "aggregate_level": "${group_meta.aggregate_level}",
        "stub": true
    }
    END_JSON
    cat <<-END_VERSIONS > versions.yml
    "${task.process}":
        stub: stub
    END_VERSIONS
    """
}

// Build a filesystem-safe tag from group_meta:
//   <aggregate_level>__<col1>=<val1>__<col2>=<val2>
// When there are no grouping columns, falls back to "<aggregate_level>__all".
def groupTag(group_meta) {
    def cols = group_meta.grouping_columns ?: []
    def parts = cols ? cols.collect { col -> "${col}=${group_meta[col]}" }.join('__') : 'all'
    "${group_meta.aggregate_level}__${parts}".replaceAll('[^A-Za-z0-9._=-]', '_')
}
