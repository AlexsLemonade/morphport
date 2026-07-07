include { groupTag; groupingInfoJson } from '../../../lib/morphport_utils.nf'

process COMPUTE_EMBEDDINGS {
    tag "${group_tag}"
    label 'process_medium'

    conda "${moduleDir}/environment.yml"
    container {
        workflow.containerEngine == 'singularity' && !task.ext.singularity_pull_docker_container
            ? 'oras://community.wave.seqera.io/library/python_polars_numpy_scikit-learn_umap-learn:c494cfb880b7d300'
            : 'community.wave.seqera.io/library/python_polars_numpy_scikit-learn_umap-learn:6c9c9e0c1f5c856e'
    }

    input:
    tuple val(meta), val(plate_metas), path(parquets, stageAs: 'input_parquet/input*.parquet'), path(features_file)
    val(pca_components)
    val(umap_neighbors)
    val(umap_min_dist)
    val(random_seed)
    val(max_nan_fraction)
    val(min_rows)

    output:
    tuple val(meta), path("embeddings_${group_tag}.parquet"), path("embeddings_summary_${group_tag}.json"), emit: result
    path "versions.yml", emit: versions, topic: versions

    script:
    group_tag = groupTag(meta)
    grouping_info = groupingInfoJson(meta, [plate_ids: meta.plate_ids])
    template 'compute_embeddings.py'

    stub:
    group_tag = groupTag(meta)
    """
    touch embeddings_${group_tag}.parquet
    cat <<-END_JSON > embeddings_summary_${group_tag}.json
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
