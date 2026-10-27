process COMPUTE_EMBEDDINGS {
    tag "${meta.group_tag}"
    label 'process_low'

    conda "${moduleDir}/environment.yml"
    container {
        workflow.containerEngine == 'singularity' && !task.ext.singularity_pull_docker_container
            ? 'oras://community.wave.seqera.io/library/python_ca-certificates_polars_numpy_pruned:3c1071edcf64d707'
            : 'community.wave.seqera.io/library/python_ca-certificates_polars_numpy_pruned:00040f01e2f6537f'
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
    tuple val(meta), path("embeddings_${meta.group_tag}.parquet"), path("embeddings_summary_${meta.group_tag}.json"), emit: result
    path "versions.yml", emit: versions, topic: versions

    script:
    meta_json = groovy.json.JsonOutput.toJson(meta)
    template 'compute_embeddings.py'

    stub:
    """
    touch embeddings_${meta.group_tag}.parquet
    cat <<-END_JSON > embeddings_summary_${meta.group_tag}.json
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
