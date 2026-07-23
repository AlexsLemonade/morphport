process PARTITION_CONDITIONS {
    tag "${meta.plate_id}"
    label 'process_low'
    label 'publish_optional'

    conda "${moduleDir}/environment.yml"
    container {
        workflow.containerEngine == 'singularity' && !task.ext.singularity_pull_docker_container
            ? 'oras://community.wave.seqera.io/library/python_duckdb_pandas:61bdada56f884ce7'
            : 'community.wave.seqera.io/library/python_duckdb_pandas:c0ae3ad14184b515'
    }

    input:
    tuple val(meta), path(parquet_file), val(analysis_groups)

    output:
    tuple val(meta), path("${meta.plate_id}"),         emit: partitions
    path "platemap_summary_${meta.plate_id}.json",     emit: summary
    path "versions.yml",                               emit: versions, topic: versions

    script:
    partition_columns_str = (analysis_groups.partition_columns as List).join(',')
    template 'partition_conditions.py'

    stub:
    // Emit a single Hive directory per plate using the _Absent_ sentinel for every column.
    // This matches what enumeratePlatemapCombos predicts for a column missing from the platemap,
    // so the stub setup pairs with an analysis_groups file that references columns absent
    // from the stub platemaps — no need to read the platemap here.
    condition_cols = analysis_groups.partition_columns as List
    stub_path = condition_cols.collect { col -> "${col}=_Absent_" }.join('/')
    """
    mkdir -p "${meta.plate_id}/${stub_path}"
    touch "${meta.plate_id}/${stub_path}/${meta.plate_id}_0.parquet"

    cat <<-END_JSON > platemap_summary_${meta.plate_id}.json
    {
        "plate_id": "${meta.plate_id}",
        "condition_columns": ${groovy.json.JsonOutput.toJson(condition_cols)},
        "partitions": ${groovy.json.JsonOutput.toJson(condition_cols.collectEntries { x -> [(x): [:]] })}
    }
    END_JSON

    cat <<-END_VERSIONS > versions.yml
    "${task.process}":
        stub: x.y.z
    END_VERSIONS
    """
}
