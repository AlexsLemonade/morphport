// Scores the phenotypic activity of each test condition in one analysis group: whether its
// reference-normalized per-well profiles are more similar to each other than to the comparison
// reference wells, measured as mean average precision (mAP) with copairs.
process PHENOTYPIC_ACTIVITY {
    tag "${meta.group_tag}"
    label 'process_low'

    conda "${moduleDir}/environment.yml"
    container {
        workflow.containerEngine == 'singularity' && !task.ext.singularity_pull_docker_container
            ? 'oras://community.wave.seqera.io/library/python_ca-certificates_pandas_pyarrow_pruned:c39156323fa82207'
            : 'community.wave.seqera.io/library/python_ca-certificates_pandas_pyarrow_pruned:0d1928234e2813c4'
    }

    input:
    // profiles and features_file are the outputs of PYCYTOMINER_REFERENCE_NORMALIZE
    tuple val(meta), path(profiles), path(features_file)
    // Map of named scoring options; see meta.yml for the required keys
    val(options)

    output:
    tuple val(meta), path("phenotypic_activity_${meta.group_tag}.parquet"), path("phenotypic_activity_summary_${meta.group_tag}.json"), emit: result
    tuple val(task.process), val(meta.group_tag), path("phenotypic_activity_warnings_${meta.group_tag}.txt"), emit: warnings, optional: true, topic: warnings
    path "versions.yml", emit: versions, topic: versions

    script:
    assert meta.comparison_column && meta.comparison_reference
        : "PHENOTYPIC_ACTIVITY: group ${meta.group_tag} has no comparison_column or comparison_reference"
    // Every option must be given; other keys are ignored
    def option_keys = ['distance_metric', 'null_method', 'null_size', 'fdr_threshold', 'seed', 'min_wells']
    def missing_keys = option_keys - options.keySet()
    assert !missing_keys
        : "PHENOTYPIC_ACTIVITY: options is missing keys ${missing_keys}"
    meta_json = groovy.json.JsonOutput.toJson(meta)
    options_json = groovy.json.JsonOutput.toJson(options)
    template 'phenotypic_activity.py'

    stub:
    """
    touch phenotypic_activity_${meta.group_tag}.parquet
    cat <<-END_JSON > phenotypic_activity_summary_${meta.group_tag}.json
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
