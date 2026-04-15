process CYTOTABLE_CONVERT {
    tag "${meta.plate_id}"
    label 'process_medium'

    conda "${moduleDir}/environment.yml"
    container {
        workflow.containerEngine == 'singularity' && !task.ext.singularity_pull_docker_container
            ? 'https://community-cr-prod.seqera.io/docker/registry/v2/blobs/sha256/83/830f89b36c91fedc0d1f6608844f45cf615327abe03272a11ea0513ed446dc46/data'
            : 'community.wave.seqera.io/library/pip_cytotable:2c29a8bfc594d5ea'
    }

    input:
    tuple val(meta), path(sqlite)

    output:
    tuple val(meta), path("${meta.plate_id}.parquet"), emit: parquet
    path "versions.yml", emit: versions

    script:
    template 'cytotable_convert.py'

    stub:
    """
    touch ${meta.plate_id}.parquet
    touch versions.yml
    """
}
