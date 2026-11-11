// Normalizes the per-well profiles of one analysis group to the group's comparison reference wells
// (reference normalization), separately for each plate, using pycytominer.normalize.
// Takes and emits the same tuple shape as PYCYTOMINER_FEATURE_SELECT, so downstream modules can
// read either feature-selected or reference-normalized profiles.
process PYCYTOMINER_REFERENCE_NORMALIZE {
    tag "${meta_in.group_tag}"
    label 'process_low'
    label 'publish_skip_parquet'

    conda "${moduleDir}/environment.yml"
    container {
        workflow.containerEngine == 'singularity' && !task.ext.singularity_pull_docker_container
            ? 'oras://community.wave.seqera.io/library/python_ca-certificates_pandas_pyarrow_pruned:77fbd4b29f56c6c0'
            : 'community.wave.seqera.io/library/python_ca-certificates_pandas_pyarrow_pruned:d60b17a9b7cc60c9'
    }

    input:
    tuple val(meta_in), val(plate_metas), path(parquets, stageAs: 'input_parquet/input*.parquet'), path(features_file)
    val(normalize_method)

    output:
    tuple val(meta),
        val(plate_metas),
        path("reference_normalized_${meta_in.group_tag}.parquet"),
        path("reference_normalized_features_${meta_in.group_tag}.txt"),
        emit: result
    path "reference_normalization_summary_${meta_in.group_tag}.json", emit: summary
    tuple val(task.process), val(meta_in.group_tag), path("reference_normalization_warnings_${meta_in.group_tag}.txt"), emit: warnings, optional: true, topic: warnings
    path "versions.yml", emit: versions, topic: versions

    script:
    // spherize is not supported: it needs many more reference wells than a single plate has
    assert normalize_method in ["mad_robustize", "standardize", "robustize"]
        : "PYCYTOMINER_REFERENCE_NORMALIZE: normalize_method must be one of 'mad_robustize', 'standardize', or 'robustize', got '${normalize_method}'"
    assert meta_in.comparison_column && meta_in.comparison_reference
        : "PYCYTOMINER_REFERENCE_NORMALIZE: group ${meta_in.group_tag} has no comparison_column or comparison_reference"
    meta = meta_in + [reference_normalized: true, reference_normalization_method: normalize_method]
    meta_json = groovy.json.JsonOutput.toJson(meta_in)
    template 'pycytominer_reference_normalize.py'

    stub:
    meta = meta_in + [reference_normalized: true, reference_normalization_method: normalize_method]
    """
    touch reference_normalized_${meta_in.group_tag}.parquet
    # Write a placeholder feature so downstream filters that drop empty
    # feature lists still see this group during stub runs.
    echo "Cells_stub_feature" > reference_normalized_features_${meta_in.group_tag}.txt
    cat <<-END_JSON > reference_normalization_summary_${meta_in.group_tag}.json
    {
        "group_tag": "${meta_in.group_tag}",
        "aggregate_level": "${meta_in.aggregate_level}",
        "stub": true
    }
    END_JSON
    cat <<-END_VERSIONS > versions.yml
    "${task.process}":
        stub: x.y.z
    END_VERSIONS
    """
}
