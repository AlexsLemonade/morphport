include { groupTag; groupingInfoJson } from '../../../../lib/morphport_utils.nf'

process PYCYTOMINER_FEATURE_SELECT {
    tag "${group_tag}"
    label 'process_low'
    label 'publish_skip_parquet'

    conda "${moduleDir}/environment.yml"
    container {
        workflow.containerEngine == 'singularity' && !task.ext.singularity_pull_docker_container
            ? 'oras://community.wave.seqera.io/library/python_pandas_pyarrow_pip_pycytominer:7f48ab6b722820f9'
            : 'community.wave.seqera.io/library/python_pandas_pyarrow_pip_pycytominer:db10e902a4043cb1'
    }

    input:
    tuple val(meta), val(plate_metas), path(parquets, stageAs: 'input_parquet/input*.parquet')
    val(options)

    output:
    // Pass the original staged parquets through alongside a text file of selected feature
    // names; downstream steps filter columns on read rather than consuming a rewritten parquet.
    // includeInputs lets Nextflow re-emit the staged inputs as outputs of this process.
    tuple val(meta), val(plate_metas), path("input_parquet/*.parquet", includeInputs: true), path("features_${group_tag}.txt"), emit: result
    path "feature_select_summary_${group_tag}.json", emit: summary
    path "versions.yml", emit: versions, topic: versions

    script:
    operations = options.operations
    na_cutoff = options.na_cutoff
    corr_threshold = options.corr_threshold
    corr_method = options.corr_method
    freq_cut = options.freq_cut
    unique_cut = options.unique_cut
    min_rows_stats = options.min_rows_stats
    outlier_cutoff = options.outlier_cutoff
    noise_removal_perturb_groups = options.noise_removal_perturb_groups
    noise_removal_stdev_cutoff = options.noise_removal_stdev_cutoff
    per_channel_correlation = options.per_channel_correlation
    def allowed_ops = ['variance_threshold', 'correlation_threshold', 'drop_na_columns', 'blocklist', 'drop_outliers', 'noise_removal']
    def ops_list = operations.tokenize(',').collect { it.trim() }
    assert ops_list.every { it in allowed_ops }
        : "PYCYTOMINER_FEATURE_SELECT: operations must be a comma-separated subset of ${allowed_ops}, got '${operations}'"
    group_tag = groupTag(meta)
    grouping_info = groupingInfoJson(meta, [aggregate_level: meta.aggregate_level])
    template 'pycytominer_feature_select.py'

    stub:
    group_tag = groupTag(meta)
    """
    # Write a placeholder feature so downstream filters that drop empty
    # feature lists (e.g. COMPUTE_EMBEDDINGS) still see this group during stub runs.
    echo "Cells_stub_feature" > features_${group_tag}.txt
    cat <<-END_JSON > feature_select_summary_${group_tag}.json
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
