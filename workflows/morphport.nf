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
include { PARTITION_PROFILES     } from '../modules/local/partition_profiles'
include { PYCYTOMINER_ANNOTATE             } from '../modules/local/pycytominer/annotate'
include { PYCYTOMINER_AGGREGATE            } from '../modules/local/pycytominer/aggregate'
include { PYCYTOMINER_PLATE_NORMALIZE      } from '../modules/local/pycytominer/plate_normalize'
include { PYCYTOMINER_FEATURE_SELECT       } from '../modules/local/pycytominer/feature_select'
include { COMPUTE_EMBEDDINGS               } from '../modules/local/compute_embeddings'
include { HIERARCHICAL_CLUSTERING_PROFILES } from '../modules/local/hierarchical_clustering_profiles'
include { HIERARCHICAL_CLUSTERING_FEATURES } from '../modules/local/hierarchical_clustering_features'
include { PYCYTOMINER_REFERENCE_NORMALIZE  } from '../modules/local/pycytominer/reference_normalize'
include { PHENOTYPIC_ACTIVITY              } from '../modules/local/phenotypic_activity'
include { CREATE_ANALYSIS_GROUPS           } from '../subworkflows/local/create_analysis_groups'
include { partitionKey                     } from '../subworkflows/local/create_analysis_groups'

/*
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    RUN MAIN WORKFLOW
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
*/

workflow MORPHPORT {
    take:
    ch_samplesheet       // channel: samplesheet read in from --input
    analysis_groups_file // string: path to the analysis groups file (null for a single group per aggregate level)
    outdir

    main:

    // Parse the analysis groups file, check the groups against every plate's platemap
    // and build the analysis groups lookup (see CREATE_ANALYSIS_GROUPS).
    // Downstream steps use the samplesheet emitted by CREATE_ANALYSIS_GROUPS rather than ch_samplesheet
    // so no process starts until the checks pass; it also adds the partition columns to platemap_columns.
    CREATE_ANALYSIS_GROUPS(
        ch_samplesheet,
        analysis_groups_file,
        params.platemap_well_column,
    )
    ch_analysis_groups = CREATE_ANALYSIS_GROUPS.out.analysis_groups

    ch_validated = CREATE_ANALYSIS_GROUPS.out.samplesheet
        .map { meta -> meta + [aggregate_level: 'cell'] }
        .branch { meta ->
            skip_cytotable: meta.cytotable_preset == 'skip'
            convert: true
        }

    // when skipping CytoTable, use the existing parquet file(s) at parquet_path (may be a glob for split files)
    ch_parquet = ch_validated.skip_cytotable.map { meta ->
        [meta, files(meta.parquet_path)]
    }

    CYTOTABLE_CONVERT(ch_validated.convert.map { meta -> [meta, file(meta.sqlite_path)] })
    ch_parquet = ch_parquet.mix(CYTOTABLE_CONVERT.out.parquet)

    // annotate cells with metadata from the platemap
    PYCYTOMINER_ANNOTATE(
        ch_parquet.map { meta, parquet -> [meta, parquet, file(meta.platemap_file, checkIfExists: true)] },
        params.platemap_well_column,
    )

    // Add to metadata whether edge wells (those on the border of the plate) are included.
    // meta.edge_wells_included is read by COSMICQC_FILTER to determine whether to drop edge wells,
    // and is carried into the group meta so analysis summaries can report it.
    // edge_well_filter is 'on' (remove edge wells) or 'off'; true and false (booleans or text) are the same as 'on' and 'off'.
    def edge_well_filter = params.edge_well_filter as String
    if (!(edge_well_filter in ['on', 'off', 'true', 'false'])) {
        error "Invalid edge_well_filter value '${params.edge_well_filter}': expected 'on', 'off', true, or false"
    }
    def edge_wells_included = edge_well_filter in ['off', 'false']
    ch_pre_cosmicqc = PYCYTOMINER_ANNOTATE.out.result
        .map { meta, parquet -> [meta + [edge_wells_included: edge_wells_included], parquet] }

    COSMICQC_FILTER(ch_pre_cosmicqc, file(params.cosmicqc_filter_file))

    ch_filtered = COSMICQC_FILTER.out.result.map { meta, parquet, summary ->
        // skipped conditions are reported through the warnings topic
        def stats = new groovy.json.JsonSlurper().parse(summary)
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

    // make per-well profiles by aggregating the per-cell profiles with pycytominer
    PYCYTOMINER_AGGREGATE(ch_filtered, params.pycytominer_aggregate_operation)

    // combine per-well and per-cell profiles into a single channel
    ch_pre_normalize = ch_filtered.mix(PYCYTOMINER_AGGREGATE.out.result)
        .map { meta, parquet ->
            // add the correct normalization method to the tuple based on the aggregate level
            def normalize_method = meta.aggregate_level == 'cell'
                ? params.pycytominer_plate_normalize_method_cell
                : params.pycytominer_plate_normalize_method_well
            [meta, parquet, normalize_method]
        }
        .branch { _meta, _parquet, normalize_method ->
            // skip normalization if method is 'none'
            normalize: normalize_method != 'none'
            skip:      true
        }

    PYCYTOMINER_PLATE_NORMALIZE(
        ch_pre_normalize.normalize.map { meta, parquet, normalize_method ->
            [meta, parquet, params.pycytominer_plate_normalize_reference_profiles, normalize_method]
        },
    )

    // combine back normalized and non-normalized files
    ch_normalized = PYCYTOMINER_PLATE_NORMALIZE.out.result.mix(
        ch_pre_normalize.skip.map { meta, parquet, _normalize_method -> [meta, parquet] }
    )

    // Partition profiles by the partition columns
    // This partitions both the per-cell and per-well profiles;
    // we separate them out again later using the aggregate_level metadata.
    // The analysis_groups value channel carries the partition column set;
    // branch on it so PARTITION_PROFILES only runs when there is something to partition.
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

    PARTITION_PROFILES(ch_prepartition.partition)
    ch_partitioned = PARTITION_PROFILES.out.partitions
        // expand each Hive directory into one tuple per partition with the partition column
        // values from the partition manifest added to meta
        .flatMap { meta, partition_dir, manifest -> expandHivePartitions(meta, partition_dir, manifest) }
        // the skip branch wasn't partitioned, so just pass the files through with the original meta
        .mix(ch_prepartition.skip.map { meta, parquet, _partition_columns -> [meta, [parquet] as List] })

    ch_grouped = createGroupChannels(ch_partitioned, ch_analysis_groups)

    PYCYTOMINER_FEATURE_SELECT(
        ch_grouped,
        [
            operations: params.feature_select_operations,
            na_cutoff: params.feature_select_na_cutoff,
            corr_threshold: params.feature_select_corr_threshold,
            corr_method: params.feature_select_corr_method,
            freq_cut: params.feature_select_freq_cut,
            unique_cut: params.feature_select_unique_cut,
            min_rows_stats: params.feature_select_min_rows_stats,
            outlier_cutoff: params.feature_select_outlier_cutoff,
            noise_removal_perturb_groups: params.feature_select_noise_removal_perturb_groups,
            noise_removal_stdev_cutoff: params.feature_select_noise_removal_stdev_cutoff,
            per_channel_correlation: params.feature_select_per_channel_correlation,
        ],
    )

    // Filter groups with an empty feature list before analysis steps
    ch_feature_selected = PYCYTOMINER_FEATURE_SELECT.out.result
        .branch { _meta, _plate_metas, _parquets, features ->
            has_features: features.size() > 0
            empty: true
        }

    ch_feature_selected.empty.subscribe { meta, _plate_metas, _parquets, _features ->
        log.warn "Skipping analysis steps for group ${meta.group_tag}: no features selected upstream."
    }

    COMPUTE_EMBEDDINGS(
        ch_feature_selected.has_features,
        [
            pca_components: params.embedding_pca_components,
            umap_neighbors: params.embedding_umap_neighbors,
            umap_min_dist: params.embedding_umap_min_dist,
            seed: params.random_seed,
            max_nan_fraction: params.profile_max_nan_fraction,
            min_rows: params.embedding_min_rows,
        ],
    )

    // cluster the profiles on either the feature-selected profiles or their PCs;
    // empty lists stand in for the optional features file and embeddings summary
    ch_clustering_profiles_input = params.clustering_profiles_input == 'pcs'
        ? COMPUTE_EMBEDDINGS.out.result
            .map { meta, embeddings_parquet, summary_json -> [meta, [embeddings_parquet], [], summary_json] }
        : ch_feature_selected.has_features
            .map { meta, _plate_metas, parquets, features -> [meta, parquets, features, []] }

    HIERARCHICAL_CLUSTERING_PROFILES(
        ch_clustering_profiles_input,
        params.clustering_profiles_distance_metric,
        params.profile_max_nan_fraction,
        params.clustering_max_profiles,
    )

    // cluster the selected features from the full feature-selected matrix
    HIERARCHICAL_CLUSTERING_FEATURES(
        ch_feature_selected.has_features,
        params.clustering_features_linkage_method,
        params.clustering_features_distance_metric,
        params.profile_max_nan_fraction,
    )

    // Per-well groups with a comparison column and reference values are normalized to their reference wells
    // (reference normalization) and scored for phenotypic activity.
    // Groups in which no partition holds reference wells can't be reference normalized,
    // so they are dropped from further analysis along this path
    ch_reference_groups = ch_feature_selected.has_features
        .filter { meta, _plate_metas, _parquets, _features ->
            meta.aggregate_level == 'well' && meta.comparison_column && meta.reference_values
        }
        .branch { meta, plate_metas, _parquets, _features ->
            has_reference: hasReferencePartition(meta, plate_metas)
            no_reference: true
        }

    ch_reference_groups.no_reference.subscribe { meta, _plate_metas, _parquets, _features ->
        log.warn "Skipping PYCYTOMINER_REFERENCE_NORMALIZE and PHENOTYPIC_ACTIVITY for group ${meta.group_tag}: no wells with a ${meta.reference_column} value in ${meta.reference_values}."
    }

    PYCYTOMINER_REFERENCE_NORMALIZE(
        ch_reference_groups.has_reference
             // drop plate_metas
            .map { meta, _plate_metas, parquets, features -> [meta, parquets, features] },
        params.pycytominer_reference_normalize_method,
    )

    // Reference normalization writes an empty features file for groups it skips
    // (e.g., too few reference wells, or no features with nonzero reference spread)
    ch_reference_normalized = PYCYTOMINER_REFERENCE_NORMALIZE.out.result
        .branch { _meta, _parquet, features ->
            has_features: features.size() > 0
            empty: true
        }

    ch_reference_normalized.empty.subscribe { meta, _parquet, _features ->
        log.warn "Skipping PHENOTYPIC_ACTIVITY for group ${meta.group_tag}: reference normalization skipped the group (see its reference normalization summary)."
    }

    // Run phenotypic activity (mAP scores) on reference-normalized groups
    PHENOTYPIC_ACTIVITY(
        ch_reference_normalized.has_features,
        [
            distance_metric: params.phenotypic_activity_distance_metric,
            null_method: params.phenotypic_activity_null,
            null_size: params.phenotypic_activity_null_size,
            fdr_threshold: params.phenotypic_activity_fdr_threshold,
            seed: params.random_seed,
            min_wells: params.consensus_min_wells,
        ],
    )

    //
    // Log warnings sent to the 'warnings' topic by any process,
    // and save them to a single file in pipeline_info (only written if there are any warnings).
    // Each entry is [task.process, identifier (e.g. plate_id), file with one warning per line].
    //
    def ch_warnings = channel.topic("warnings")
        .flatMap { process, id, warnings_file ->
            def process_name = process[process.lastIndexOf(':') + 1..-1]
            warnings_file.readLines()
                .findAll { line -> line.trim() }
                .collect { line -> "${process_name} (${id}): ${line}" }
        }

    ch_warnings.subscribe { warning -> log.warn(warning) }

    ch_warnings.collectFile(
        storeDir: "${params.outdir}/pipeline_info",
        name: "morphport_warnings_${params.trace_report_suffix}.txt",
        sort: true,
        newLine: true,
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
// Given a Hive partition directory and the manifest written alongside it by PARTITION_PROFILES,
// find the parquet files in each partition, returning a list of tuples of the form:
//   [meta + partition_values, list_of_parquet_files]
// The partition column values come from the manifest rather than the directory names, because
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
        .findAll { _partition_values, parquets -> parquets }
}

//
// Build a channel of all analysis groups across aggregate levels.
// Each tuple from ch_partitioned is looked up in the analysis groups (see buildAnalysisGroups)
// by its aggregate level and partition key (partition column values plus project), and fanned
// out into one tuple per group it belongs to, keyed by groupKey(group_meta, size) so groupTuple
// can release each group as soon as size items have arrived.
// A partition that belongs to no group at its aggregate level (filtered out or excluded) emits nothing.
//
def createGroupChannels(ch_partitioned, ch_analysis_groups) {
    def ch_grouping = ch_partitioned
        // add the analysis groups lookup to each ch_partitioned tuple
        .combine(ch_analysis_groups)
        .flatMap { meta, parquets, groups ->
            def aggregate_level = meta.aggregate_level
            def partition_key = partitionKey(meta, groups.partition_columns)
            def aggregate_level_groups = groups[aggregate_level] ?: [:]
            // every partition value comes from a platemap (or samplesheet) read during
            // initialisation, so a partition missing from the lookup is a pipeline bug
            if (!aggregate_level_groups.containsKey(partition_key)) {
                error("Internal error: no analysis groups lookup entry for ${aggregate_level} partition ${partition_key} (plate '${meta.plate_id}')")
            }
            aggregate_level_groups[partition_key].collect { group_meta, expected_size ->
                // Carry whether edge wells are included into the group meta, so analysis summaries can report it.
                // Every partition has the same value (it comes from params.edge_well_filter).
                def key_data = meta.edge_wells_included != null
                    ? group_meta + [edge_wells_included: meta.edge_wells_included]
                    : group_meta
                [groupKey(key_data, expected_size), meta, parquets]
            }
        }
    // do the grouping, flatten the parquet lists, and add plate_ids and the plate normalization settings
    // (plate_normalization_method and plate_normalization_reference_profiles) to the group meta so
    // downstream consumers can read them without walking metas. Returns tuples of the form:
    //   [group_meta, list_of_meta, list_of_parquet_files]
    return ch_grouping.groupTuple(by: 0, remainder: true)
        .map { group_key, metas, parquet_lists ->
            def group_meta = group_key + [plate_ids: metas.collect { it -> it.plate_id }]
            group_meta = group_meta + plateNormalizationSettings(group_meta.group_tag, metas)
            [group_meta, metas, parquet_lists.flatten()]
        }
}

//
// Whether any partition of an analysis group holds reference wells: true when any partition meta's value in the
// group's reference_column is one of its reference_values. Values are compared as text, as in the reference
// normalization and phenotypic activity modules. The reference column is always a partition column, and wells
// with a blank ('_NA_') reference value are already left out of the group, so no data needs to be read.
//
def hasReferencePartition(group_meta, partition_metas) {
    def reference_values = group_meta.reference_values.collect { value -> value as String }
    return partition_metas.any { partition_meta ->
        (partition_meta[group_meta.reference_column] as String) in reference_values
    }
}

//
// The plate normalization settings of an analysis group, read from its partition metas:
//   [plate_normalization_method: ..., plate_normalization_reference_profiles: ...]
// Every plate in a group shares one aggregate level, so the settings come from the same params and must agree;
// values that differ are a pipeline bug. Values are null when plate normalization was skipped (method 'none'),
// since those partition metas lack the keys.
//
def plateNormalizationSettings(group_tag, partition_metas) {
    def keys = ['plate_normalization_method', 'plate_normalization_reference_profiles']
    return keys.collectEntries { key ->
        def values = partition_metas.collect { partition_meta -> partition_meta[key] }.unique()
        if (values.size() > 1) {
            error("Internal error: partitions of group '${group_tag}' have different ${key} values: ${values}")
        }
        [(key): values ? values[0] : null]
    }
}
