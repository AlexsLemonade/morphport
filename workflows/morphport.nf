/*
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    IMPORT MODULES / SUBWORKFLOWS / FUNCTIONS
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
*/
include { paramsSummaryMap       } from 'plugin/nf-schema'
include { softwareVersionsToYAML } from '../subworkflows/nf-core/utils_nfcore_pipeline'
include { methodsDescriptionText } from '../subworkflows/local/utils_nfcore_morphport_pipeline'
include { CYTOTABLE_CONVERT                        } from '../modules/local/cytotable'
include { COSMICQC_FILTER                          } from '../modules/local/cosmicqc'
include { PARTITION_CONDITIONS                     } from '../modules/local/partition_conditions'
include { PYCYTOMINER_AGGREGATE; PYCYTOMINER_ANNOTATE; PYCYTOMINER_NORMALIZE } from '../modules/local/pycytominer'

/*
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    RUN MAIN WORKFLOW
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
*/

workflow MORPHPORT {
    take:
    ch_samplesheet     // channel: samplesheet read in from --input
    ch_analysis_groups // value:   nested map of analysis groups → sub-combo → expected size
    outdir

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

    // annotate cells with metadata from the platemap
    PYCYTOMINER_ANNOTATE(
        ch_parquet.map { meta, parquet -> [meta, parquet, file(meta.platemap_file, checkIfExists: true)] },
        params.platemap_well_column,
    )

    COSMICQC_FILTER(PYCYTOMINER_ANNOTATE.out.result, file(params.cosmicqc_filter_file))

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

    // combine well- and cell-level data into a single channel
    ch_pre_normalize = ch_filtered.mix(PYCYTOMINER_AGGREGATE.out.result)
        .map { meta, parquet ->
            // add the correct normalization method to the tuple based on the aggregate level
            def normalize_method = meta.aggregate_level == 'cell'
                ? params.pycytominer_normalize_method_cell
                : params.pycytominer_normalize_method_well
            [meta, parquet, normalize_method]
        }
        .branch { _meta, _parquet, normalize_method ->
            // skip normalization if method is 'none'
            normalize: normalize_method != 'none'
            skip:      true
        }

    PYCYTOMINER_NORMALIZE(
        ch_pre_normalize.normalize.map { meta, parquet, normalize_method ->
            [meta, parquet, params.pycytominer_normalize_samples, normalize_method]
        },
    )

    // combine back normalized and non-normalized files
    ch_normalized = PYCYTOMINER_NORMALIZE.out.result.mix(
        ch_pre_normalize.skip.map { meta, parquet, _normalize_method -> [meta, parquet] }
    )

    // partition analyses by condition
    // this does both cell and well level partitioning;
    // we can separate these out again later using the aggregate_level metadata
    PARTITION_CONDITIONS(ch_normalized, params.platemap_condition_columns)
    ch_partitioned = PARTITION_CONDITIONS.out.partitions
        .flatMap { meta, partition_dir -> expandHivePartitions(meta, partition_dir) }

    ch_grouped = createGroupChannels(ch_partitioned, ch_analysis_groups)

    ch_grouped.view { gm, metas, parquets ->
        "${gm.grouping_columns} [${gm.aggregate_level}] → ${gm.subMap(gm.grouping_columns)} — ${metas.size()} plate(s), ${parquets.size()} file(s)"
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

    def ch_collated_versions = softwareVersionsToYAML(topic_versions.versions_file)
        .mix(topic_versions_string)
        .collectFile(
            storeDir: "${params.outdir}/pipeline_info",
            name: 'morphport_software_' + 'versions.yml',
            sort: true,
            newLine: true,
        )

    emit:
    versions = ch_collated_versions
}

/*
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    FUNCTIONS
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
*/

// Build a single mixed channel of all condition groupings across both aggregate levels.
// Each tuple from ch_partitioned is fanned out into one tuple per matching grouping
// (those whose aggregate_level matches the partition's level), keyed by groupKey(..., size)
// so groupTuple can release each group as soon as size items have arrived.
def createGroupChannels(ch_partitioned, ch_analysis_groups) {
    return ch_partitioned
        .combine(ch_analysis_groups)
        .flatMap { meta, parquets, groups ->
            def agg_level = meta.aggregate_level
            def level_data = groups[agg_level] ?: [:]
            level_data.collect { grouping_cols, subcombo_sizes ->
                def sub_combo = meta.subMap(grouping_cols)
                def key_data  = sub_combo + [
                    aggregate_level  : agg_level,
                    grouping_columns : grouping_cols,
                ]
                // Predicted counts can be 0 if the platemap doesn't enumerate the actual
                // combo (e.g. DuckDB's __HIVE_DEFAULT_PARTITION__ for NULL values, or the
                // stub's _NA_ placeholder). Fall back to MAX_VALUE so groupTuple waits for
                // channel close instead of emitting an underfilled group.
                def expected = subcombo_sizes[sub_combo] ?: Integer.MAX_VALUE
                [groupKey(key_data, expected), meta, parquets]
            }
        }
        .groupTuple(by: 0, remainder: true)
        .map { gk, metas, parquet_lists -> [gk, metas, parquet_lists.flatten()] }
}

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
