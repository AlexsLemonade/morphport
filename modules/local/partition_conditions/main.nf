process PARTITION_CONDITIONS {
    tag "${meta.plate_id}"
    label 'process_low'
    label 'publish_optional'

    conda "${moduleDir}/environment.yml"
    container {
        workflow.containerEngine == 'singularity' && !task.ext.singularity_pull_docker_container
            ? 'oras://community.wave.seqera.io/library/python_duckdb_pandas:171e24539f18e905'
            : 'community.wave.seqera.io/library/python_duckdb_pandas:60551b8f9a81baa9'
    }

    input:
    tuple val(meta), path(parquet_file), path(platemap_file)
    val partition_columns

    output:
    tuple val(meta), path("${meta.plate_id}"),         emit: partitions
    path "${meta.plate_id}_platemap_summary.json",     emit: summary
    path "versions.yml",                               emit: versions, topic: versions

    script:
    template 'partition_conditions.py'

    stub:
    // define the partition columns, ensuring they are prefixed with 'Metadata_'
    def cols = partition_columns.split(',')
        .collect { col -> col.startsWith('Metadata_') ? col : "Metadata_${col}" }
    """
    mkdir -p "${meta.plate_id}/Metadata_gene_allele=NA"
    touch "${meta.plate_id}/Metadata_gene_allele=NA/${meta.plate_id}_0.parquet"
    cat <<-END_JSON > ${meta.plate_id}_platemap_summary.json
    {
        "plate_id": "${meta.plate_id}",
        "platemap_file": "${platemap_file}",
        "partition_columns": ${groovy.json.JsonOutput.toJson(cols)},
        "partitions": ${groovy.json.JsonOutput.toJson(cols.collectEntries { [(it): [:]] })}
    }
    END_JSON

    cat <<-END_VERSIONS > versions.yml
    "${task.process}":
        stub: stub
    END_VERSIONS
    """
}
