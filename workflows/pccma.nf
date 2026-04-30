/*
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    IMPORT MODULES / SUBWORKFLOWS / FUNCTIONS
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
*/
include { paramsSummaryMap       } from 'plugin/nf-schema'
include { softwareVersionsToYAML } from '../subworkflows/nf-core/utils_nfcore_pipeline'
include { methodsDescriptionText } from '../subworkflows/local/utils_nfcore_pccma_pipeline'
include { CYTOTABLE_CONVERT      } from '../modules/local/cytotable'
include { COSMICQC_FILTER        } from '../modules/local/cosmicqc'
include { PARTITION_CONDITIONS   } from '../modules/local/partition_conditions'

/*
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    RUN MAIN WORKFLOW
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
*/

workflow PCCMA {
    take:
    ch_samplesheet // channel: samplesheet read in from --input

    main:

    ch_validated = ch_samplesheet
        .map { meta -> [meta, file(meta.plate_dir)] }
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

    ch_filtered = COSMICQC_FILTER.out.result
        // add metadata from filter results to meta object
        .map { meta, parquet, summary ->
            def stats = new groovy.json.JsonSlurper().parse(summary)
            if (stats.results.skipped_conditions) {
                stats.results.skipped_conditions.each { condition, features ->
                    log.warn "coSMicQC [${meta.plate_id}]: skipped condition '${condition}' — features not found in parquet: ${features.join(', ')}"
                }
            }
            [meta + [
                cosmicqc_cells_input     : stats.results.cells_input,
                cosmicqc_cells_output    : stats.results.cells_output,
                cosmicqc_fraction_removed: stats.results.fraction_removed,
            ], parquet]
        }

    PARTITION_CONDITIONS(
        ch_filtered.map { meta, parquet -> [meta, parquet, file(meta.platemap_file, checkIfExists: true)] },
        params.well_condition_columns
    )

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
            name: 'pccma_software_' + 'versions.yml',
            sort: true,
            newLine: true,
        )
        .set { ch_collated_versions }

    emit:
    versions = ch_collated_versions
}
