process PYCYTOMINER_FEATURE_SELECT {
    tag "${prefix}"
    label 'process_low'

    conda "${moduleDir}/environment.yml"
    container {
        workflow.containerEngine == 'singularity' && !task.ext.singularity_pull_docker_container
            ? 'oras://community.wave.seqera.io/library/python_pandas_pyarrow_pip_pycytominer:7f48ab6b722820f9'
            : 'community.wave.seqera.io/library/python_pandas_pyarrow_pip_pycytominer:db10e902a4043cb1'
    }

    input:
    tuple val(meta), val(plate_metas), path(parquets, stageAs: 'input_parquet/*.parquet')
    val(operations)
    val(corr_threshold)
    val(freq_cut)
    val(unique_cut)

    output:
    tuple val(meta), val(plate_metas), path("${prefix}_feature_selected.parquet", arity: 1), emit: result
    path "${prefix}_summary.json", emit: summary
    path "versions.yml", emit: versions, topic: versions

    script:
    prefix = groupTag(meta)
    grouping_info = groovy.json.JsonOutput.toJson([
        grouping_values: (meta.grouping_columns ?: []).collectEntries { col -> [(col): meta[col]] },
        aggregate_level: meta.aggregate_level,
    ])
    template 'pycytominer_feature_select.py'

    stub:
    prefix = groupTag(meta)
    """
    touch ${prefix}_feature_selected.parquet
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
