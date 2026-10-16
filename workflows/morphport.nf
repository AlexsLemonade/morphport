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
include { PYCYTOMINER_ANNOTATE       } from '../modules/local/pycytominer/annotate'
include { PYCYTOMINER_AGGREGATE      } from '../modules/local/pycytominer/aggregate'
include { PYCYTOMINER_NORMALIZE      } from '../modules/local/pycytominer/normalize'
include { PYCYTOMINER_FEATURE_SELECT } from '../modules/local/pycytominer/feature_select'
include { COMPUTE_EMBEDDINGS         } from '../modules/local/compute_embeddings'
include { HIERARCHICAL_CLUSTERING    } from '../modules/local/hierarchical_clustering'
include { CREATE_ANALYSIS_GROUPS     } from '../subworkflows/local/create_analysis_groups'
include { partitionKey               } from '../subworkflows/local/create_analysis_groups'

/*
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    RUN MAIN WORKFLOW
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
*/

workflow MORPHPORT {
    take:
    ch_samplesheet       // channel: samplesheet read in from --input
    analysis_groups_file // string: path to the analysis groups file (null for a single group per level)
    outdir

    main:

    // Parse the analysis groups file, check the groups against every plate's platemap
    // and build the analysis groups lookup (see CREATE_ANALYSIS_GROUPS).
    // Downstream steps use the samplesheet emitted by CREATE_ANALYSIS_GROUPS rather than ch_samplesheet
    // so no process starts until the checks pass; it also adds the partition columns to plate_metadata_columns.
    CREATE_ANALYSIS_GROUPS(
        ch_samplesheet,
        analysis_groups_file,
        params.platemap_well_column,
    )
    ch_analysis_groups = CREATE_ANALYSIS_GROUPS.out.analysis_groups

    ch_validated = CREATE_ANALYSIS_GROUPS.out.samplesheet
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

    // Add to metadata whether edge wells (those on the border of the plate) are included.
    // meta.edge_wells_included is read by COSMICQC_FILTER to determine whether to drop edge wells.
    // for the `both` case, we double everything up: one set with edge wells included (true)
    // and one with them excluded (false)
    ch_annotated = PYCYTOMINER_ANNOTATE.out.result
    if (params.edge_well_filter == 'on') {
        ch_pre_cosmicqc = ch_annotated.map { meta, parquet -> [meta + [edge_wells_included: false], parquet] }
    } else if (params.edge_well_filter == 'off') {
        ch_pre_cosmicqc = ch_annotated.map { meta, parquet -> [meta + [edge_wells_included: true], parquet] }
    } else if (params.edge_well_filter == 'both') {
        ch_pre_cosmicqc = ch_annotated
            .map { meta, parquet -> [meta + [edge_wells_included: true], parquet] }
            .mix(ch_annotated.map { meta, parquet -> [meta + [edge_wells_included: false], parquet] })
    } else {
        error "Invalid edge_well_filter value '${params.edge_well_filter}': expected 'off', 'on', or 'both'"
    }

    COSMICQC_FILTER(ch_pre_cosmicqc, file(params.cosmicqc_filter_file))

    ch_filtered = COSMICQC_FILTER.out.result.map { meta, parquet, summary ->
        def stats = new groovy.json.JsonSlurper().parse(summary)
        if (stats.cosmicqc_filtering.skipped_conditions) {
            stats.cosmicqc_filtering.skipped_conditions.each { condition, features ->
                log.debug("coSMicQC [${meta.plate_id}]: skipped condition '${condition}' — features not found in parquet: ${features.join(', ')}")
            }
        }
        [
            meta + [
                cosmicqc_cells_input: stats.cosmicqc_filtering.cells_input,
                cosmicqc_cells_output: stats.cosmicqc_filtering.cells_output,
                cosmicqc_fraction_removed: stats.cosmicqc_filtering.fraction_removed,
                edge_wells_removed: stats.edge_filtering.wells_removed,
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
    // Only the partition columns are passed on, so edits to the rest of the lookup
    // (group names, membership) don't invalidate cached partitioning tasks on -resume.
    // Note that because ch_analysis_groups is a value channel,
    // all values in ch_normalized will take the same branch path
    ch_prepartition = ch_normalized
        .combine(ch_analysis_groups.map { groups -> [groups.partition_columns] })
        .branch { _meta, _parquet, partition_columns ->
            partition: partition_columns.size() > 0
            skip:      true
        }

    PARTITION_CONDITIONS(ch_prepartition.partition)
    ch_partitioned = PARTITION_CONDITIONS.out.partitions
        // expand each Hive directory into one tuple per partition with the conditions
        // from the partition manifest added to meta
        .flatMap { meta, partition_dir, manifest -> expandHivePartitions(meta, partition_dir, manifest) }
        // the skip branch wasn't partitioned, so just pass the files through with the original meta
        .mix(ch_prepartition.skip.map { meta, parquet, _partition_columns -> [meta, [parquet] as List] })

    ch_grouped = createGroupChannels(ch_partitioned, ch_analysis_groups)

    PYCYTOMINER_FEATURE_SELECT(
        ch_grouped,
        params.feature_select_operations,
        params.feature_select_na_cutoff,
        params.feature_select_corr_threshold,
        params.feature_select_corr_method,
        params.feature_select_freq_cut,
        params.feature_select_unique_cut,
        params.feature_select_min_rows_stats,
        params.feature_select_outlier_cutoff,
        params.feature_select_noise_removal_perturb_groups ?: '',
        params.feature_select_noise_removal_stdev_cutoff,
        params.feature_select_per_channel_correlation,
    )

    // Filter groups with an empty feature list before analysis steps
    ch_feature_selected = PYCYTOMINER_FEATURE_SELECT.out.result
        .branch { _meta, _plate_metas, _parquets, features ->
            has_features: features.size() > 0
            empty: true
        }

    ch_feature_selected.empty.subscribe { meta, _plate_metas, _parquets, _features ->
        log.warn "Skipping COMPUTE_EMBEDDINGS for group ${meta.group_tag}: no features selected upstream."
    }

    COMPUTE_EMBEDDINGS(
        ch_feature_selected.has_features,
        params.embedding_pca_components,
        params.embedding_umap_neighbors,
        params.embedding_umap_min_dist,
        params.random_seed,
        params.embedding_max_nan_fraction,
        params.embedding_min_rows,
    )

    HIERARCHICAL_CLUSTERING(
        COMPUTE_EMBEDDINGS.out.result,
        params.clustering_linkage_method,
        params.clustering_distance_metric,
        params.clustering_max_samples,
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
// Given a Hive partition directory and the manifest written alongside it by PARTITION_CONDITIONS,
// find the parquet files in each partition, returning a list of tuples of the form:
//   [meta + partition_conditions, list_of_parquet_files]
// The partition conditions come from the manifest rather than the directory names, because
// DuckDB percent-encodes values in directory names (e.g. 'drug A' → 'drug%20A').
// Each manifest entry has the form:
//   [path: 'Metadata_treatment=drug%20A', values: [Metadata_treatment: 'drug A']]
//
def expandHivePartitions(meta, partition_dir, manifest_file) {
    def manifest = new groovy.json.JsonSlurper().parse(manifest_file) as List
    return manifest
        .collect { partition ->
            def dir = partition_dir.resolve(partition['path'] as String)
            def parquets = dir.listFiles()?.findAll { f -> f.name.endsWith('.parquet') } ?: []
            [meta + (partition['values'] as Map), parquets]
        }
        .findAll { _conditions, parquets -> parquets }
}

//
// Build a channel of all analysis groups across aggregate levels.
// Each tuple from ch_partitioned is looked up in the analysis groups (see buildAnalysisGroups)
// by its aggregate level and partition key (partition column values plus project), and fanned
// out into one tuple per group it belongs to, keyed by groupKey(group_meta, size) so groupTuple
// can release each group as soon as size items have arrived.
// A partition that belongs to no group at its level (filtered out or excluded) emits nothing.
//
def createGroupChannels(ch_partitioned, ch_analysis_groups) {
    def ch_grouping = ch_partitioned
        // add the analysis groups lookup to each ch_partitioned tuple
        .combine(ch_analysis_groups)
        .flatMap { meta, parquets, groups ->
            def agg_level = meta.aggregate_level
            def partition_key = partitionKey(meta, groups.partition_columns)
            def level_groups = groups[agg_level] ?: [:]
            // every partition value comes from a platemap (or samplesheet) read during
            // initialisation, so a partition missing from the lookup is a pipeline bug
            if (!level_groups.containsKey(partition_key)) {
                error("Internal error: no analysis groups lookup entry for ${agg_level} partition ${partition_key} (plate '${meta.plate_id}')")
            }
            level_groups[partition_key].collect { group_meta, expected_size ->
                // Keep edge-included/edge-excluded data (edge_well_filter='both') in separate groups.
                // Each variant gets every partition, so the expected size is the same for both.
                def key_data = params.edge_well_filter == 'both'
                    ? group_meta + [edge_wells_included: meta.edge_wells_included]
                    : group_meta
                [groupKey(key_data, expected_size), meta, parquets]
            }
        }
    // do the grouping, flatten the parquet lists, and add plate_ids to the group meta so
    // downstream consumers can read it without walking metas. Returns tuples of the form:
    //   [group_meta, list_of_meta, list_of_parquet_files]
    return ch_grouping.groupTuple(by: 0, remainder: true)
        .map { group_key, metas, parquet_lists ->
            def group_meta = group_key + [plate_ids: metas.collect { it -> it.plate_id }]
            [group_meta, metas, parquet_lists.flatten()]
        }
}
