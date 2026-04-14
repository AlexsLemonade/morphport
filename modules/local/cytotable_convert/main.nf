process CYTOTABLE_CONVERT {
    tag "$meta.plate_id"
    label 'process_medium'

    conda "${moduleDir}/environment.yml"

    input:
    tuple val(meta), path(sqlite)

    output:
    tuple val(meta), path("${meta.plate_id}.parquet"), emit: parquet
    path "versions.yml",                        emit: versions

    script:
    template 'cytotable_convert.py'
}
