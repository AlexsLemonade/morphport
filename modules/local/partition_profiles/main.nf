process PARTITION_PROFILES {
    tag "${meta.plate_id}"
    label 'process_low'
    label 'publish_optional'

    conda "${moduleDir}/environment.yml"
    container {
        workflow.containerEngine == 'singularity' && !task.ext.singularity_pull_docker_container
            ? 'oras://community.wave.seqera.io/library/python_ca-certificates_duckdb_pandas:86b81ac2f85fdc9c'
            : 'community.wave.seqera.io/library/python_ca-certificates_duckdb_pandas:c0e4f3dcd3f763ab'
    }

    input:
    tuple val(meta), path(parquet_file), val(partition_columns)

    output:
    tuple val(meta), path("${meta.plate_id}"), path("partitions_${meta.plate_id}.json"), emit: partitions
    path "platemap_summary_${meta.plate_id}.json",     emit: summary
    path "versions.yml",                               emit: versions, topic: versions

    script:
    partition_columns_str = (partition_columns as List).join(',')
    template 'partition_profiles.py'

    stub:
    // Emit one Hive directory per combination of partition values in the plate's (tab-separated) stub platemap,
    // so stub partitions match the analysis groups; a column missing from the platemap gets '_Absent_'.
    partition_cols = partition_columns as List
    stub_partitions = file(meta.platemap_file).splitCsv(header: true, sep: '\t')
        .collect { row -> partition_cols.collectEntries { col -> [(col): row[col - 'Metadata_'] ?: '_Absent_'] } }
        .unique()
        .collect { values -> [path: values.collect { col, value -> "${col}=${value}" }.join('/'), values: values] }
    stub_dirs = stub_partitions.collect { partition -> "${meta.plate_id}/${partition.path}" }
    """
    for dir in ${stub_dirs.join(' ')}; do
        mkdir -p \$dir
        touch \$dir/${meta.plate_id}_0.parquet
    done
    echo '${groovy.json.JsonOutput.toJson(stub_partitions)}' > partitions_${meta.plate_id}.json

    cat <<-END_JSON > platemap_summary_${meta.plate_id}.json
    {
        "plate_id": "${meta.plate_id}",
        "partition_columns": ${groovy.json.JsonOutput.toJson(partition_cols)},
        "partitions": ${groovy.json.JsonOutput.toJson(partition_cols.collectEntries { x -> [(x): [:]] })}
    }
    END_JSON

    cat <<-END_VERSIONS > versions.yml
    "${task.process}":
        stub: x.y.z
    END_VERSIONS
    """
}
