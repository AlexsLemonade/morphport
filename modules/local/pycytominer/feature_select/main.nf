process PYCYTOMINER_FEATURE_SELECT {
    tag "${prefix}"
    label 'process_medium'
    label 'publish_skip_parquet'

    conda "${moduleDir}/environment.yml"
    container {
        workflow.containerEngine == 'singularity' && !task.ext.singularity_pull_docker_container
            ? 'oras://community.wave.seqera.io/library/python_pandas_pyarrow_pip_pycytominer:7f48ab6b722820f9'
            : 'community.wave.seqera.io/library/python_pandas_pyarrow_pip_pycytominer:db10e902a4043cb1'
    }

    input:
    tuple val(meta), val(plate_metas), path(parquets, stageAs: 'input_parquet/input*.parquet')
    val(operations)
    val(na_cutoff)
    val(corr_threshold)
    val(freq_cut)
    val(unique_cut)
    val(min_rows_stats)

    output:
    // Pass the original staged parquets through alongside a text file of selected feature
    // names; downstream steps filter columns on read rather than consuming a rewritten parquet.
    // includeInputs lets Nextflow re-emit the staged inputs as outputs of this process.
    tuple val(meta), val(plate_metas), path("input_parquet/*.parquet", includeInputs: true), path("${prefix}_features.txt"), emit: result
    path "${prefix}_summary.json", emit: summary
    path "versions.yml", emit: versions, topic: versions

    script:
    def allowed_ops = ['variance_threshold', 'correlation_threshold', 'drop_na_columns', 'blocklist', 'drop_outliers', 'noise_removal']
    def ops_list = operations.tokenize(',').collect { it.trim() }
    assert ops_list.every { it in allowed_ops }
        : "PYCYTOMINER_FEATURE_SELECT: operations must be a comma-separated subset of ${allowed_ops}, got '${operations}'"
    prefix = groupTag(meta)
    grouping_info = groovy.json.JsonOutput.toJson([
        grouping_values: (meta.grouping_columns ?: []).collectEntries { col -> [(col): meta[col]] },
        aggregate_level: meta.aggregate_level,
    ])
    template 'pycytominer_feature_select.py'

    stub:
    prefix = groupTag(meta)
    """
    touch ${prefix}_features.txt
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

def groupTag(meta) {
    def cols = meta.grouping_columns ?: []
    def parts = cols ? cols.collect { col -> "${col}=${meta[col]}" }.join('__') : 'all'
    "${meta.aggregate_level}__${parts}".replaceAll('[^A-Za-z0-9._=-]', '_')
}
