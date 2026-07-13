process COSMICQC_FILTER {
    tag "${meta.plate_id}"
    label 'process_low'

    conda "${moduleDir}/environment.yml"
    container {
        workflow.containerEngine == 'singularity' && !task.ext.singularity_pull_docker_container
            ? 'oras://community.wave.seqera.io/library/python_pandas_pyarrow_tabulate_pruned:b58e918e2e8f7107'
            : 'community.wave.seqera.io/library/python_pandas_pyarrow_tabulate_pruned:8ad93d3a1a344c67'
    }

    input:
    tuple val(meta), path(parquet_file)
    path cosmicqc_filter_file

    output:
    tuple val(meta), path("filtered_${meta.plate_id}.parquet"), path("cosmicqc_summary_${meta.plate_id}.json"), emit: result
    path "cosmicqc_outliers_${meta.plate_id}.csv.gz", emit: outliers
    path "versions.yml", emit: versions, topic: versions

    script:
    template 'cosmicqc_filter.py'

    stub:
    """
    touch filtered_${meta.plate_id}.parquet
    cat <<-END_JSON > cosmicqc_summary_${meta.plate_id}.json
    {
        "plate_id": "${meta.plate_id}",
        "filter_settings": [],
        "cosmicqc_filtering": {
            "cells_input": 0,
            "cells_output": 0,
            "fraction_removed": "NA",
            "condition_outlier_counts": {},
            "skipped_conditions": {}
        },
        "edge_filtering": {
            "excluded": ${meta.edge_wells == 'excluded'},
            "plate_size": null,
            "wells_removed": 0,
            "edge_wells": [],
            "cells_input": 0,
            "cells_output": 0
        }
    }
    END_JSON
    # gzip file with no content to create an empty outliers file
    echo | gzip > cosmicqc_outliers_${meta.plate_id}.csv.gz
    cat <<-END_VERSIONS > versions.yml
    "${task.process}":
        stub: stub
    END_VERSIONS
    """
}
