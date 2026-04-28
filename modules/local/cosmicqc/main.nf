process COSMICQC_FILTER {
    tag "${meta.plate_id}"
    label 'process_low'

    conda "${moduleDir}/environment.yml"
    container {
        workflow.containerEngine == 'singularity' && !task.ext.singularity_pull_docker_container
            ? 'oras://community.wave.seqera.io/library/python_pandas_pyarrow_tabulate_pruned:b7ef0fb922c2acc9'
            : 'community.wave.seqera.io/library/python_pandas_pyarrow_tabulate_pruned:f6644cf617ab9240'
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
    cat <<-END_JSON > ${meta.plate_id}_cosmicqc_summary.json
    {
        "plate_id": "${meta.plate_id}",
        "filter_settings": [],
        "results": {
            "cells_input": 0,
            "cells_output": 0,
            "fraction_removed": 0.0,
            "condition_outlier_counts": {},
            "skipped_conditions": {}
        }
    }
    END_JSON
    touch ${meta.plate_id}_cosmicqc_outliers.csv.gz
    cat <<-END_VERSIONS > versions.yml
    "${task.process}":
        stub: stub
    END_VERSIONS
    """
}
