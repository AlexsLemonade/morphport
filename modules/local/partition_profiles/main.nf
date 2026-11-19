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
    // Emit one Hive directory per combination of partition values in the plate's platemap (see stubPartitionValues),
    // so stub partitions match the analysis groups lookup.
    // Directory names are percent-encoded (as DuckDB writes them), which also keeps them safe in the shell;
    // the manifest records the exact values, and is written with a quoted heredoc so no value is shell-expanded.
    partition_cols = partition_columns as List
    stub_partitions = stubPartitionValues(meta.platemap_file, partition_cols)
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
// The unique combinations of partition column values in a platemap, for the stub: a list of maps of
// partition column to value. The platemap is read as when the analysis groups are built (readPlatemap and
// enumeratePlatemapPartitionValues in the create_analysis_groups subworkflow), so the stub's partitions match
// the analysis groups lookup:
// - fields are comma-separated in .csv files and tab-separated otherwise, with '"' for quoting
// - column names and values are trimmed, and column names get the Metadata_ prefix if they lack it
// - columns with blank names, and rows with only blank values, are left out
// - blank values are '_NA_', and partition columns missing from the platemap are '_Absent_'
// The platemap was already validated when the analysis groups were built, so it is not checked again here.
//
def stubPartitionValues(platemap_path, partition_cols) {
    def platemap = file(platemap_path)
    def sep = platemap.extension.toLowerCase() == 'csv' ? ',' : '\t'
    def lines = platemap.splitCsv(sep: sep, quote: '"')

    def columns = lines[0].collect { col ->
        def name = col.trim()
        if (!name) {
            return ''
        }
        return name.startsWith('Metadata_') ? name : "Metadata_${name}".toString()
    }
    def named_idx = columns.indices.findAll { idx -> columns[idx] }

    def rows = lines.drop(1)
        // fields[idx] is null for missing trailing fields
        .collect { fields -> named_idx.collectEntries { idx -> [(columns[idx]): fields[idx]?.trim() ?: ''] } }
        .findAll { row -> row.values().any() }

    return rows
        .collect { row ->
            partition_cols.collectEntries { col ->
                def value = row.containsKey(col) ? (row[col] ?: '_NA_') : '_Absent_'
                [(col): value]
            }
        }
        .unique()
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
