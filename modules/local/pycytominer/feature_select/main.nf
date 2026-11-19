process PYCYTOMINER_FEATURE_SELECT {
    tag "${meta.group_tag}"
    label 'process_low'
    label 'publish_skip_parquet'

    conda "${moduleDir}/environment.yml"
    container {
        workflow.containerEngine == 'singularity' && !task.ext.singularity_pull_docker_container
            ? 'oras://community.wave.seqera.io/library/python_ca-certificates_pandas_pyarrow_pruned:77fbd4b29f56c6c0'
            : 'community.wave.seqera.io/library/python_ca-certificates_pandas_pyarrow_pruned:d60b17a9b7cc60c9'
    }

    input:
    tuple val(meta), val(plate_metas), path(parquets, stageAs: 'input_parquet/input*.parquet')
    // Map of named feature selection options; see meta.yml for the required keys
    val(options)

    output:
    // Pass the original staged parquets through alongside a text file of selected feature
    // names; downstream steps filter columns on read rather than consuming a rewritten parquet.
    // includeInputs lets Nextflow re-emit the staged inputs as outputs of this process.
    tuple val(meta), val(plate_metas), path("input_parquet/*.parquet", includeInputs: true), path("features_${meta.group_tag}.txt"), emit: result
    path "feature_select_summary_${meta.group_tag}.json", emit: summary
    path "versions.yml", emit: versions, topic: versions

    script:
    // Every option must be given; other keys are ignored
    def option_keys = [
        'operations', 'na_cutoff', 'corr_threshold', 'corr_method', 'freq_cut', 'unique_cut', 'min_rows_stats',
        'outlier_cutoff', 'noise_removal_perturb_groups', 'noise_removal_stdev_cutoff', 'per_channel_correlation',
    ]
    def missing_keys = option_keys - options.keySet()
    assert !missing_keys
        : "PYCYTOMINER_FEATURE_SELECT: options is missing keys ${missing_keys}"
    def allowed_ops = ['variance_threshold', 'correlation_threshold', 'drop_na_columns', 'blocklist', 'drop_outliers', 'noise_removal']
    def ops_list = options.operations.tokenize(',').collect { op -> op.trim() }
    assert ops_list.every { op -> op in allowed_ops }
        : "PYCYTOMINER_FEATURE_SELECT: operations must be a comma-separated subset of ${allowed_ops}, got '${options.operations}'"
    meta_json = groovy.json.JsonOutput.toJson(meta)
    options_json = groovy.json.JsonOutput.toJson(options)
    template 'pycytominer_feature_select.py'

    stub:
    """
    # Write a placeholder feature so downstream filters that drop empty
    # feature lists (e.g. COMPUTE_EMBEDDINGS) still see this group during stub runs.
    echo "Cells_stub_feature" > features_${meta.group_tag}.txt
    cat <<-END_JSON > feature_select_summary_${meta.group_tag}.json
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
