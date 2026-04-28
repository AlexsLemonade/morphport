process COSMICQC_FILTER {
    tag "${meta.plate_id}"
    label 'process_low'

    conda "${moduleDir}/environment.yml"
    container {
        workflow.containerEngine == 'singularity' && !task.ext.singularity_pull_docker_container
            ? 'TBD'
            : 'TBD'
    }

    input:
    tuple val(meta), path(parquet_file)
    path cosmicqc_filter_file

    output:
    tuple val(meta), path("${meta.plate_id}_filtered.parquet"), path("${meta.plate_id}_cosmicqc_summary.json"), emit: result
    path "${meta.plate_id}_cosmicqc_outliers.csv.gz", emit: outliers
    path "versions.yml", emit: versions, topic: versions

    script:
    template 'cosmicqc_filter.py'

    stub:
    """
    touch ${meta.plate_id}_filtered.parquet
    touch ${meta.plate_id}_cosmicqc_summary.json
    touch ${meta.plate_id}_cosmicqc_outliers.csv.gz
    cat <<-END_VERSIONS > versions.yml
    "${task.process}":
        stub: stub
    END_VERSIONS
    """
}
