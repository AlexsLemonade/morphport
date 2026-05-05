/*
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    IMPORT MODULES / SUBWORKFLOWS / FUNCTIONS
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
*/
include { paramsSummaryMap       } from 'plugin/nf-schema'
include { softwareVersionsToYAML } from '../subworkflows/nf-core/utils_nfcore_pipeline'
include { methodsDescriptionText } from '../subworkflows/local/utils_nfcore_morphport_pipeline'
include { CYTOTABLE_CONVERT      } from '../modules/local/cytotable'
include { COSMICQC_FILTER        } from '../modules/local/cosmicqc'
include { PARTITION_CONDITIONS   } from '../modules/local/partition_conditions'
include { PYCYTOMINER_AGGREGATE  } from '../modules/local/pycytominer'

/*
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    RUN MAIN WORKFLOW
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
*/

workflow MORPHPORT {
    take:
    ch_samplesheet // channel: samplesheet read in from --input

    main:

    ch_validated = ch_samplesheet
        .map { meta -> [meta + [aggregate_level: 'cell'], file(meta.plate_dir)] }
        .branch { it ->
            skip_cytotable: it[0].cytotable_preset == 'skip'
            convert: true
        }

    ch_parquet = ch_validated.skip_cytotable.map { meta, plate_dir ->
        [meta, file("${plate_dir}/*.parquet")[0]]
    }

    CYTOTABLE_CONVERT(ch_validated.convert)
    ch_parquet = ch_parquet.mix(CYTOTABLE_CONVERT.out.parquet)

    COSMICQC_FILTER(ch_parquet, file(params.cosmicqc_filter_file))

    ch_filtered = COSMICQC_FILTER.out.result.map { meta, parquet, summary ->
        def stats = new groovy.json.JsonSlurper().parse(summary)
        if (stats.results.skipped_conditions) {
            stats.results.skipped_conditions.each { condition, features ->
                log.debug("coSMicQC [${meta.plate_id}]: skipped condition '${condition}' — features not found in parquet: ${features.join(', ')}")
            }
        }
        [
            meta + [
                cosmicqc_cells_input: stats.results.cells_input,
                cosmicqc_cells_output: stats.results.cells_output,
                cosmicqc_fraction_removed: stats.results.fraction_removed,
            ],
            parquet,
        ]
    }

    // make well-level aggregates with pycytominer
    PYCYTOMINER_AGGREGATE(ch_filtered, params.pycytominer_aggregate_operation)

    // partition analyses by condition
    // this does both cell and well level partitioning;
    // we can separate these out again later using the aggregate_level metadata
    PARTITION_CONDITIONS(
        ch_filtered.mix(PYCYTOMINER_AGGREGATE.out.result)
            .map { meta, parquet -> [meta, parquet, file(meta.platemap_file, checkIfExists: true)] },
        params.platemap_well_column,
        params.platemap_condition_columns,
    )
    ch_partitioned = PARTITION_CONDITIONS.out.partitions
        .flatMap { meta, partition_dir -> expandHivePartitions(meta, partition_dir) }

    // temporary view to see what happened with partitioning
    ch_partitioned.view { meta, parquets ->
        def conditions = meta.subMap(meta.keySet().findAll { it -> it.startsWith('Metadata_') })
        "${meta.plate_id}, ${meta.aggregate_level}: ${conditions} — ${parquets.size()} parquet file(s)"
    }

    //
    // Collate and save software versions
    //
    def topic_versions = channel.topic("versions")
        .distinct()
        .branch { entry ->
            versions_file: entry instanceof Path
            versions_tuple: true
        }

    def topic_versions_string = topic_versions.versions_tuple
        .map { process, tool, version ->
            [process[process.lastIndexOf(':') + 1..-1], "  ${tool}: ${version}"]
        }
        .groupTuple(by: 0)
        .map { process, tool_versions ->
            tool_versions.unique().sort()
            "${process}:\n${tool_versions.join('\n')}"
        }

    softwareVersionsToYAML(topic_versions.versions_file)
        .mix(topic_versions_string)
        .collectFile(
            storeDir: "${params.outdir}/pipeline_info",
            name: 'morphport_software_' + 'versions.yml',
            sort: true,
            newLine: true,
        )
        .set { ch_collated_versions }

    emit:
    versions = ch_collated_versions
}

/*
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    FUNCTIONS
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
*/

def expandHivePartitions(meta, partition_dir) {
    def results = []
    partition_dir.eachDirRecurse { dir ->
        def parquets = dir.listFiles()?.findAll { f -> f.name.endsWith('.parquet') } ?: []
        if (parquets) {
            def rel = partition_dir.relativize(dir)
            // iterate over the relative path segments to build a map of conditions,
            // e.g. ['Metadata_gene_allele=_NA_', 'Metadata_treatment=drugA'] -> [Metadata_gene_allele: '_NA_', Metadata_treatment: 'drugA']
            def conditions = rel
                .collect { segment ->
                    def parts = segment.toString().split('=', 2)
                    [(parts[0]): parts[1]]
                }
                .sum([:])
            // merge list of maps into a single map
            results << [meta + conditions, parquets]
        }
    }
    return results
}
