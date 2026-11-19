include { enumeratePlatemapPartitionValues } from '../../../subworkflows/local/create_analysis_groups'

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
    // Emit one Hive directory per combination of partition values in the plate's platemap.
    // The values come from enumeratePlatemapPartitionValues, which predicts each plate's partitions when the
    // analysis groups are built, so stub partitions always match the analysis groups lookup.
    // Directory names are percent-encoded (as DuckDB writes them), which also keeps them safe in the shell;
    // the manifest records the exact values, and is written with a quoted heredoc so no value is shell-expanded.
    partition_cols = partition_columns as List
    stub_partitions = enumeratePlatemapPartitionValues(meta.platemap_file, partition_cols)
        .collect { values ->
            def path_parts = values.collect { col, value -> "${percentEncode(col)}=${percentEncode(value)}" }
            [path: path_parts.join('/'), values: values]
        }
    stub_dirs = stub_partitions.collect { partition -> "${meta.plate_id}/${partition.path}" }
    """
    for dir in ${stub_dirs.join(' ')}; do
        mkdir -p "\$dir"
        touch "\$dir/${meta.plate_id}_0.parquet"
    done

    cat <<-'END_MANIFEST' > partitions_${meta.plate_id}.json
    ${groovy.json.JsonOutput.toJson(stub_partitions)}
    END_MANIFEST

    cat <<-'END_JSON' > platemap_summary_${meta.plate_id}.json
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

//
// Percent-encode a value for a Hive partition directory name, as DuckDB does when partitioning.
// Only letters, digits, '.', '-', and '_' are left as is, so the result is also safe in the shell.
//
def percentEncode(value) {
    return java.net.URLEncoder.encode(value as String, 'UTF-8')
        .replace('+', '%20')
        .replace('*', '%2A')
}
