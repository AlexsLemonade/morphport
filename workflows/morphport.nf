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
include { PYCYTOMINER_AGGREGATE; PYCYTOMINER_ANNOTATE; PYCYTOMINER_NORMALIZE } from '../modules/local/pycytominer'
include { COMPUTE_EMBEDDINGS     } from '../modules/local/compute_embeddings'

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

    // Partition analyses by condition
    // This does both cell and well level partitioning;
    // we separate them out again later using the aggregate_level metadata.
    // The analysis_groups value channel carries the partition column set;
    // branch on it so PARTITION_CONDITIONS only runs when there is something to partition.
    // Note that because ch_analysis_groups is a value channel,
    // all values in ch_normalized will take the same branch path
    ch_prepartition = ch_normalized
        .combine(ch_analysis_groups)
        .branch { _meta, _parquet, groups ->
            partition: groups.partition_columns.size() > 0
            skip:      true
        }

    PARTITION_CONDITIONS(ch_prepartition.partition)
    ch_partitioned = PARTITION_CONDITIONS.out.partitions
        // expand each Hive directory into one tuple per partition with the conditions
        // parsed from the directory name added to meta
        .flatMap { meta, partition_dir -> expandHivePartitions(meta, partition_dir) }
        // the skip branch wasn't partitioned, so just pass the files through with the original meta
        .mix(ch_prepartition.skip.map { meta, parquet, _groups -> [meta, [parquet] as List] })

    ch_grouped = createGroupChannels(ch_partitioned, ch_analysis_groups)

    COMPUTE_EMBEDDINGS(
        ch_grouped,
        params.embedding_pca_components,
        params.embedding_umap_neighbors,
        params.embedding_umap_min_dist,
        params.random_seed,
        params.embedding_max_nan_fraction,
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

//
// Given a Hive partition directory, recursively find all parquet files and extract the partition
// conditions from the directory structure, returning a list of tuples of the form:
//   [meta + partition_conditions, list_of_parquet_files]

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

//
// Build a channel of all condition groupings across aggregate levels.
// Each tuple from ch_partitioned is fanned out into one tuple per matching grouping
// (those whose aggregate_level matches the partition's level), keyed by groupKey(..., size)
// so groupTuple can release each group as soon as size items have arrived.
//
def createGroupChannels(ch_partitioned, ch_analysis_groups) {
    def ch_grouping = ch_partitioned
        // add full map to each ch_partitioned tuple
        .combine(ch_analysis_groups)
        // for each partition tuple, find the matching grouping(s) based on aggregate level and add the group key info
        .flatMap { meta, parquets, groups ->
            def agg_level = meta.aggregate_level
            // look up the groupings for this aggregate level
            //if not present, an empty list (no grouping)
            def level_data = groups[agg_level] ?: [:]
            // for each set of grouping values, create a tuple keyed by groupKey(..., size)
            // with the grouping columns and values in the meta
            level_data.collect { grouping_cols, grouping_value_sizes ->
                // get the values of the grouping columns for this partition by subsetting the
                // partition conditions from the meta.
                // Add project so analysis groups stay separated by project
                def grouping_value = meta.subMap(grouping_cols + ['project'])
                def key_data  = grouping_value + [
                    aggregate_level  : agg_level,
                    grouping_columns : grouping_cols,
                ]
                // Look up the expected size for this grouping
                // A missing predicted size means the partition's grouping values weren't seen in any
                // platemap during init
                // Warn and set the expected value to max so emission happens at end with remainder: true.
                def expected_size = grouping_value_sizes.get(grouping_value, Integer.MAX_VALUE)
                if (expected_size == Integer.MAX_VALUE) {
                    log.warn "morphport: no predicted size for ${agg_level} grouping ${grouping_cols} grouping_value ${grouping_value}"
                }

                [groupKey(key_data, expected_size), meta, parquets]
            }
        }
    // do the grouping, flatten the parquet lists, and add plate_ids to the group meta so
    // downstream consumers can read it without walking metas. Returns tuples of the form:
    //   [group_meta, list_of_meta, list_of_parquet_files]
    return ch_grouping.groupTuple(by: 0, remainder: true)
        .map { group_key, metas, parquet_lists ->
            def group_meta = group_key + [plate_ids: metas.collect { it.plate_id }]
            [group_meta, metas, parquet_lists.flatten()]
        }
}
