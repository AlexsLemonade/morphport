//
// Subworkflow with functionality specific to the AlexsLemonade/morphport pipeline
//

/*
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    IMPORT FUNCTIONS / MODULES / SUBWORKFLOWS
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
*/

include { UTILS_NFSCHEMA_PLUGIN     } from '../../nf-core/utils_nfschema_plugin'
include { paramsSummaryMap          } from 'plugin/nf-schema'
include { samplesheetToList         } from 'plugin/nf-schema'
include { paramsHelp                } from 'plugin/nf-schema'
include { completionEmail           } from '../../nf-core/utils_nfcore_pipeline'
include { completionSummary         } from '../../nf-core/utils_nfcore_pipeline'
include { UTILS_NFCORE_PIPELINE     } from '../../nf-core/utils_nfcore_pipeline'
include { UTILS_NEXTFLOW_PIPELINE   } from '../../nf-core/utils_nextflow_pipeline'

/*
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    SUBWORKFLOW TO INITIALISE PIPELINE
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
*/

workflow PIPELINE_INITIALISATION {

    take:
    version           // boolean: Display version and exit
    validate_params   // boolean: Boolean whether to validate parameters against the schema at runtime
    monochrome_logs   // boolean: Do not use coloured log outputs
    nextflow_cli_args //   array: List of positional nextflow CLI args
    outdir            //  string: The output directory where the results will be saved
    input             //  string: Path to input samplesheet
    help              // boolean: Display help message and exit
    help_full         // boolean: Show the full help message
    show_hidden       // boolean: Show hidden parameters in the help message

    main:

    ch_versions = channel.empty()

    //
    // Print version and exit if required and dump pipeline parameters to JSON file
    //
    UTILS_NEXTFLOW_PIPELINE (
        version,
        true,
        outdir,
        workflow.profile.tokenize(',').intersect(['conda', 'mamba']).size() >= 1
    )

    //
    // Validate parameters and generate parameter summary to stdout
    //

    def before_text = ""
    def after_text = ""
    if (monochrome_logs) {
        before_text = before_text.replaceAll(/\033\[[0-9;]*m/, '')
    }

    command = "nextflow run ${workflow.manifest.name} -profile <docker/singularity/.../institute> --input samplesheet.csv --outdir <OUTDIR>"

    UTILS_NFSCHEMA_PLUGIN (
        workflow,
        validate_params,
        null,
        help,
        help_full,
        show_hidden,
        before_text,
        after_text,
        command,
        false
    )

    //
    // Check config provided to the pipeline
    //
    UTILS_NFCORE_PIPELINE (
        nextflow_cli_args
    )

    //
    // Cross-parameter validation: schema can't express constraints like
    // "comma-separated list must be drawn from this set" or "param X is
    // required when param Y contains value Z", so handle those checks here.
    //
    def feature_select_ops = params.feature_select_operations.tokenize(',').collect { it.trim() }
    def allowed_feature_select_ops = ['variance_threshold', 'correlation_threshold', 'drop_na_columns', 'blocklist', 'drop_outliers', 'noise_removal']
    def invalid_ops = feature_select_ops - allowed_feature_select_ops
    if (invalid_ops) {
        error("feature_select_operations contains unknown operations ${invalid_ops}. Allowed values: ${allowed_feature_select_ops}.")
    }
    if ('noise_removal' in feature_select_ops && !params.feature_select_noise_removal_perturb_groups) {
        error("feature_select_operations includes 'noise_removal' but feature_select_noise_removal_perturb_groups is empty. Set it to the metadata column identifying replicate groups.")
    }
    if (params.clustering_linkage_method == 'ward' && params.clustering_distance_metric != 'euclidean') {
        error("clustering_linkage_method 'ward' requires clustering_distance_metric 'euclidean', got '${params.clustering_distance_metric}'.")
    }

    //
    // Create channel from input file provided through params.input
    //

    //
    // Compute analysis groupings and the partition column set using the information in the analysis groups file
    // Since these don't depend on any channel elements, they don't need to be a channel process.
    //
    def parsed_groups = parseGroupings(params.analysis_groups_file)
    def partition_columns = groupingPartitionColumns(parsed_groups)

    channel
        .fromList(samplesheetToList(input, "${projectDir}/assets/schema_input.json"))
        .map { row -> row[0] }
        .toList()
        .map { samplesheet ->
            validateInputSamplesheet(samplesheet, partition_columns)
        }
        .flatMap()
        .set { ch_samplesheet }

    //
    // Build a value channel bundling:
    //   - partition_columns: list passed to PARTITION_CONDITIONS (if list is empty ⇒ no partitioning)
    //   - cell / well:       grouping_cols → sub_combo → expected group size, used to wrap
    //                        downstream items with groupKey(...) for eager groupTuple emit.
    // Sizes are computed from each plate's platemap so downstream groupTuple can release
    // each group as soon as it's full, rather than waiting for upstream channel close.
    //
    // Example output structure (two plates, groupings by treatment and cell_type):
    //
    //   [
    //     partition_columns: ['Metadata_treatment', 'Metadata_cell_type'],
    //     cell: [
    //       ['Metadata_treatment']: [
    //         [Metadata_treatment: 'DMSO', project: 'cpg0016']: 2,
    //         [Metadata_treatment: 'Drug_A', Project: 'cpg0016']: 1,
    //         [Metadata_treatment: '_NA_', project: 'cpg0016']: 1,
    //       ],
    //       ['Metadata_cell_type']: [...],
    //     ],
    //     well: [...]
    //   ]
    def ch_analysis_groups = ch_samplesheet
        .map { meta ->
            // add project onto every combo so analysis groups stay partitioned per-project.
            enumeratePlatemapCombos(meta.platemap_file, partition_columns)
                .collect { combo -> combo + [project: meta.project] }
        }
        .collect() // Nextflow collect into a single value channel: list of lists of combos
        .map { all_combos ->
            buildAnalysisGroups(parsed_groups, all_combos.flatten() as List) +
                [partition_columns: partition_columns]
        }

    emit:
    samplesheet     = ch_samplesheet
    analysis_groups = ch_analysis_groups
    versions        = ch_versions
}

/*
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    SUBWORKFLOW FOR PIPELINE COMPLETION
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
*/

workflow PIPELINE_COMPLETION {

    take:
    email           //  string: email address
    email_on_fail   //  string: email address sent on pipeline failure
    plaintext_email // boolean: Send plain-text email instead of HTML
    outdir          //    path: Path to output directory where results will be published
    monochrome_logs // boolean: Disable ANSI colour codes in log output

    main:
    summary_params = paramsSummaryMap(workflow, parameters_schema: "nextflow_schema.json")

    //
    // Completion email and summary
    //
    workflow.onComplete {
        if (email || email_on_fail) {
            completionEmail(
                summary_params,
                email,
                email_on_fail,
                plaintext_email,
                outdir,
                monochrome_logs,
                []
            )
        }

        completionSummary(monochrome_logs)

    }

    workflow.onError {
        log.error "Pipeline failed. Please refer to troubleshooting docs for common issues: https://nf-co.re/docs/running/troubleshooting"
    }
}

/*
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    FUNCTIONS
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
*/

//
// Validate channels from input samplesheet
//
def validateInputSamplesheet(metas, partition_columns) {
    def errors = []

    def validated_metas = metas.collect { meta ->
        // resolve plate_dir for CPG projects that omit it in the samplesheet
        def plate_dir = meta.plate_dir
        if (!plate_dir) {
            if (meta.project =~ /^cpg\d{4}/) {
                plate_dir = "s3://cellpainting-gallery/${meta.project}/${meta.source}/workspace/backend/${meta.batch}/${meta.plate_id}"
            }
            else {
                errors << ("Plate '${meta.plate_id}': project '${meta.project}'" +
                    " does not match the expected CPG format (e.g. cpg0016-jump)." +
                    " Provide 'plate_dir' explicitly in the samplesheet.")
            }
        }
        // check that plate directories contain the expected files
        if (plate_dir) {
            if (meta.cytotable_preset.contains("sqlite")) {
                def sqlite_files = file("${plate_dir}/*.sqlite")
                if (sqlite_files.isEmpty()) {
                    errors << "No sqlite file found for plate '${meta.plate_id}' at ${plate_dir}"
                }
            }
            else if (meta.cytotable_preset == "skip") {
                // skip cytotable conversion, but check that we have one parquet file for later steps
                def parquet_files = file("${plate_dir}/*.parquet")
                if (parquet_files.isEmpty()) {
                    errors << "No parquet file found for plate '${meta.plate_id}' at ${plate_dir}"
                }
                else if (parquet_files.size() > 1) {
                    errors << "Multiple parquet files found for plate '${meta.plate_id}' at ${plate_dir}: ${parquet_files.collect { f -> f.name }.join(', ')}"
                }
            }
        }

        // resolve platemap if not explicitly provided in the samplesheet
        def platemap_path = meta.platemap_file
        if (!platemap_path) {
            def platemaps_base = null
            if (meta.platemaps_dir) {
                platemaps_base = meta.platemaps_dir
            }
            else if (meta.project =~ /^cpg\d{4}/) {
                platemaps_base = "s3://cellpainting-gallery/${meta.project}/${meta.source}/workspace/metadata/platemaps"
            }
            else {
                errors << ("Plate '${meta.plate_id}': no platemap provided and project '${meta.project}'" +
                    " does not match the expected CPG format." +
                    " Provide 'platemap_file' or 'platemaps_dir' in the samplesheet.")
            }
            if (platemaps_base) {
                try {
                    platemap_path = resolvePlatemapFromDir(meta.plate_id, meta.batch, platemaps_base)
                }
                catch (Exception e) {
                    errors << "Plate '${meta.plate_id}': ${e.message}"
                }
            }
        }

        // read platemap columns so they travel with meta through the pipeline;
        // partition columns are always included even if absent from this platemap
        def platemap_cols = platemap_path ? parsePlatemapColumns(platemap_path, params.platemap_well_column, partition_columns) : []

        // update and return
        meta + [plate_dir: plate_dir, platemap_file: platemap_path, plate_metadata_columns: platemap_cols]
    }

    if (errors) {
        error(
            "Samplesheet validation failed:\n" + errors.collect { err -> "  - ${err}" }.join("\n")
        )
    }

    return validated_metas
}



//
// Resolve the platemap file path for a plate from a platemaps directory tree.
//
// Reads {platemaps_base}/{batch}/barcode_platemap.csv to map the plate barcode
// to a Plate_Map_Name, then returns the path to the corresponding .txt file at
// {platemaps_base}/{batch}/platemap/{Plate_Map_Name}.txt.
//
// Returns the resolved platemap path as a String.
// Throws an exception with a descriptive message if the barcode file, the plate
// entry, or the platemap file cannot be found.
//
def resolvePlatemapFromDir(plate_id, batch, platemaps_base) {
    def barcode_file = file("${platemaps_base}/${batch}/barcode_platemap.csv")
    if (!barcode_file.exists()) {
        throw new Exception("barcode_platemap.csv not found at ${barcode_file}")
    }
    def text = barcode_file.text
    // strip UTF-8 BOM if present
    if (text.startsWith('\uFEFF')) {
        text = text.substring(1)
    }
    def lines = text.readLines()
    if (lines.size() < 2) {
        throw new Exception("barcode_platemap.csv has no data rows: ${barcode_file}")
    }
    def headers = lines[0].split(',')*.trim()
    def barcode_idx = headers.indexOf('Assay_Plate_Barcode')
    def platemap_idx = headers.indexOf('Plate_Map_Name')
    if (barcode_idx < 0 || platemap_idx < 0) {
        throw new Exception("barcode_platemap.csv at ${barcode_file} is missing expected columns 'Assay_Plate_Barcode' and/or 'Plate_Map_Name'")
    }
    def match = lines.drop(1).find { line ->
        def parts = line.split(',')*.trim()
        parts.size() > barcode_idx && parts[barcode_idx] == plate_id
    }
    def platemap_name = match ? match.split(',')*.trim()[platemap_idx] : null
    if (!platemap_name) {
        throw new Exception("plate '${plate_id}' not found in ${barcode_file}")
    }
    def resolved = file("${platemaps_base}/${batch}/platemap/${platemap_name}.txt")
    if (!resolved.exists()) {
        throw new Exception("platemap file not found at ${resolved}")
    }
    // return the resolved platemap path as a URI string for storage in the meta map
    return resolved.toUriString()
}

//
// Ensure a column name has the "Metadata_" prefix.
//
def addMetadataPrefix(col) {
    return col.startsWith('Metadata_') ? col : 'Metadata_' + col
}

//
// Check the structure of a parsed analysis groups file (Tier 1 validation: anything knowable
// from the file alone, without reading platemaps). See docs/analysis_groups_format.md.
// Returns [errors: [...], warnings: [...]] so that every problem can be reported at once.
//
def validateGroupingStructure(parsed) {
    def errors = []
    def warnings = []
    def entry_keys = ['name', 'grouping_column', 'groups', 'always_include', 'excluded', 'filter', 'subgroup_columns']

    if (!(parsed instanceof Map)) {
        errors << "The analysis groups file must be a mapping with 'default', 'cell', and/or 'well' keys."
        return [errors: errors, warnings: warnings]
    }
    def unknown_levels = parsed.keySet().collect { key -> key as String } - ['default', 'cell', 'well']
    unknown_levels.each { key ->
        errors << "Unknown top-level key '${key}': expected 'default', 'cell', and/or 'well'."
    }

    def level_entries = ['default', 'cell', 'well'].collectEntries { level ->
        def entries = parsed.get(level)
        if (entries != null && !(entries instanceof List)) {
            errors << "Top-level key '${level}' must be a list of entries."
            entries = []
        }
        [(level): entries ?: []]
    }

    level_entries.each { level, entries ->
        entries.eachWithIndex { entry, idx ->
            def label = "Entry ${idx + 1} in '${level}'"
            if (!(entry instanceof Map)) {
                errors << "${label} ('${entry}') must be a mapping with 'name', 'grouping_column', and 'groups' keys."
                return
            }
            if (entry.name != null) {
                label = "Entry '${entry.name}' in '${level}'"
            }

            // keys
            (entry.keySet().collect { key -> key as String } - entry_keys).each { key ->
                errors << "${label}: unknown key '${key}'."
            }
            ['name', 'grouping_column', 'groups'].findAll { key -> entry.get(key) == null }.each { key ->
                errors << "${label}: missing required key '${key}'."
            }

            // name
            if (entry.name != null && !((entry.name as String) ==~ /[A-Za-z0-9_-]+/)) {
                errors << "${label}: name may contain only letters, digits, '_', and '-'."
            }

            // grouping_column
            def grouping_column = null
            if (entry.grouping_column != null) {
                if (!(entry.grouping_column instanceof String) || (entry.grouping_column as String).contains(',')) {
                    errors << "${label}: grouping_column must be a single column name; use subgroup_columns to subdivide by other columns."
                }
                else {
                    grouping_column = addMetadataPrefix(entry.grouping_column as String)
                }
            }

            // groups
            def groups = entry.groups
            if (groups instanceof Map) {
                if (groups.isEmpty()) {
                    errors << "${label}: groups mapping must define at least one group."
                }
                groups.each { group_name, values ->
                    if (!(values instanceof List)) {
                        errors << "${label}: group '${group_name}' must be a list of values."
                    }
                }
            }
            else if (groups != null && !(groups in ['one_per_value', 'all_values'])) {
                errors << "${label}: groups must be 'one_per_value', 'all_values', or a mapping of group names to lists of values."
            }

            // optional list-valued keys
            ['always_include', 'excluded', 'subgroup_columns'].each { key ->
                if (entry.get(key) != null && !(entry.get(key) instanceof List)) {
                    errors << "${label}: ${key} must be a list."
                }
            }
            if (entry.filter != null) {
                if (!(entry.filter instanceof Map)) {
                    errors << "${label}: filter must be a mapping of column names to lists of values."
                }
                else {
                    entry.filter.each { col, values ->
                        if (!(values instanceof List)) {
                            errors << "${label}: filter column '${col}' must be a list of values."
                        }
                    }
                }
            }

            // cross-key checks
            if (grouping_column) {
                if (entry.filter instanceof Map && entry.filter.keySet().any { col -> addMetadataPrefix(col as String) == grouping_column }) {
                    errors << "${label}: filter must not name the grouping_column; use excluded to drop its values."
                }
                if (entry.subgroup_columns instanceof List && entry.subgroup_columns.any { col -> addMetadataPrefix(col as String) == grouping_column }) {
                    errors << "${label}: subgroup_columns must not name the grouping_column."
                }
            }
            if (entry.always_include instanceof List && entry.excluded instanceof List) {
                def overlap = entry.always_include.collect { value -> value as String }
                    .intersect(entry.excluded.collect { value -> value as String })
                if (overlap) {
                    errors << "${label}: values ${overlap} are in both always_include and excluded."
                }
            }
            if (groups == 'all_values' && entry.always_include) {
                warnings << "${label}: always_include has no effect with 'groups: all_values'."
            }
        }
    }

    // names must be unique within each level, counting the default entries added to it
    def default_names = level_entries['default'].findAll { entry -> entry instanceof Map && entry.name != null }
        .collect { entry -> entry.name as String }
    ['cell', 'well'].each { level ->
        def names = default_names + level_entries[level]
            .findAll { entry -> entry instanceof Map && entry.name != null }
            .collect { entry -> entry.name as String }
        names.countBy { name -> name }.findAll { _name, count -> count > 1 }.each { name, _count ->
            errors << "Entry name '${name}' is used more than once in '${level}' (including 'default' entries)."
        }
    }

    return [errors: errors, warnings: warnings]
}

//
// Normalize a structurally valid parsed analysis groups file into a map of
// aggregate_level ('cell'/'well') → list of entries. Each entry has every key filled in:
//   [name, grouping_column, groups, always_include, excluded, filter, subgroup_columns]
// Column names get the Metadata_ prefix, and all values are coerced to strings to match
// the values read from platemaps. 'default' entries are placed first in each level.
//
def normalizeGroupings(parsed) {
    def default_entries = (parsed.get('default') ?: []).collect { entry -> normalizeGroupingEntry(entry) }
    return ['cell', 'well'].collectEntries { level ->
        [(level): default_entries + (parsed.get(level) ?: []).collect { entry -> normalizeGroupingEntry(entry) }]
    }
}

//
// Normalize a single analysis groups entry; see normalizeGroupings.
//
def normalizeGroupingEntry(entry) {
    def groups = entry.groups instanceof Map
        ? entry.groups.collectEntries { group_name, values -> [(group_name as String): toStringList(values)] }
        : entry.groups as String
    return [
        name: entry.name as String,
        grouping_column: addMetadataPrefix(entry.grouping_column as String),
        groups: groups,
        always_include: toStringList(entry.always_include),
        excluded: toStringList(entry.excluded),
        filter: (entry.filter ?: [:]).collectEntries { col, values -> [(addMetadataPrefix(col as String)): toStringList(values)] },
        subgroup_columns: (entry.subgroup_columns ?: []).collect { col -> addMetadataPrefix(col as String) },
    ]
}

//
// Coerce a (possibly null) list of YAML scalars to a list of strings.
//
def toStringList(values) {
    return (values ?: []).collect { value -> value as String }
}

//
// Parse analysis_groups_file into a map of aggregate_level ('cell'/'well') → list of
// normalized entries (see normalizeGroupings and docs/analysis_groups_format.md).
// Structural errors abort the run here, before any process is submitted.
// If no file is given, each level gets a single entry with no grouping column, meaning
// "one all-encompassing group per level" (no partitioning).
//
def parseGroupings(groups_file) {
    if (!groups_file) {
        def all_entry = [
            name: null,
            grouping_column: null,
            groups: 'all_values',
            always_include: [],
            excluded: [],
            filter: [:],
            subgroup_columns: [],
        ]
        return [cell: [all_entry], well: [all_entry]]
    }

    def parsed = new org.yaml.snakeyaml.Yaml().load(file(groups_file, checkIfExists: true).text)
    def validation = validateGroupingStructure(parsed)
    validation.warnings.each { warning -> log.warn("Analysis groups file: ${warning}") }
    if (validation.errors) {
        error(
            "Analysis groups file validation failed:\n" + validation.errors.collect { err -> "  - ${err}" }.join("\n")
        )
    }
    return normalizeGroupings(parsed)
}

//
// Columns PARTITION_CONDITIONS must split by: the union of every entry's grouping_column,
// subgroup_columns, and filter columns across both levels.
//
def groupingPartitionColumns(parsed_groups) {
    def entries = parsed_groups.cell + parsed_groups.well
    def columns = entries.collectMany { entry ->
        [entry.grouping_column] + entry.subgroup_columns + entry.filter.keySet()
    }
    return columns.findAll().unique() as List<String>
}

//
// Parse the columns from a platemap file, excluding the well column.
// Returns a list of column names with "Metadata_" prefix added if not already present.
// `extra_columns` (already Metadata_-prefixed) are appended so that columns required
// downstream, like the partition columns, travel with meta even if a given platemap
// happens to omit them.
//
def parsePlatemapColumns(platemap_path, platemap_well_column, extra_columns = []) {
    // read only the header line of the platemap file
    def text = file(platemap_path).newReader().readLine()
    // strip UTF-8 BOM if present
    if (text.startsWith('\uFEFF')) text = text.substring(1)

    def header_cols = text.split('\t')
        .collect { it -> it.trim() }
        .findAll { it -> it && it != platemap_well_column }
        .collect { it -> addMetadataPrefix(it) }
    return (header_cols + extra_columns).unique()
}

//
// Combine parsed analysis-group definitions with the union of platemap condition combos to
// produce a nested map: aggregate_level → grouping_cols → grouping_values → expected plate count.
//
// The structure carries both the grouping definitions (its keys) and the predicted group
// sizes (its leaves). Sizes count, for each (level, grouping_cols, grouping_values) triple, the
// number of (plate × grouping_values) pairs that match.
// They're used as the `size` argument to groupKey(...) so groupTuple can emit each group
// eagerly rather than waiting for the upstream channel to close.
//
// Sizes are upper bounds: COSMICQC_FILTER may drop wells, in which case the corresponding
// partition won't materialise and that group falls back to channel-close emission via
// remainder: true.
//
def buildAnalysisGroups(groupings, plate_combos) {
    groupings.collectEntries { agg_level, level_groupings ->
        def level_map = level_groupings.collectEntries { grouping_cols ->
            // inner map: (grouping_cols + project) values → expected plate count.
            // project is included so analysis groups stay partitioned per-project, even though
            // it is not exposed as an analysis grouping column.
            [(grouping_cols): plate_combos.countBy { it -> it.subMap(grouping_cols + ['project']) }]
        }
        [(agg_level): level_map]
    }
}

//
// Read a platemap file and return the list of unique condition combinations present on the plate.
// Each combination is a Map keyed by Metadata_-prefixed column names → row values, with the same
// sentinel encoding that pycytominer_annotate.py applies before partitioning:
//   - blank cell                                 → '_NA_'    (mirrors fillna("_NA_"))
//   - condition column absent from this platemap → '_Absent_' (mirrors the plate_metadata_columns fill)
// Keeping the encoding aligned is what lets buildAnalysisGroups predict sizes that actually match
// the keys produced by expandHivePartitions.
//
def enumeratePlatemapCombos(platemap_path, partition_columns) {
    def lines = file(platemap_path).text.readLines()
    if (lines.size() < 2) return []

    def header_line = lines[0]
    if (header_line.startsWith('\uFEFF')) header_line = header_line.substring(1)

    // Normalize headers to Metadata_-prefixed form so lookup against the already-prefixed
    // partition_columns works whether or not the platemap shipped headers with the prefix.
    def headers = header_line.split('\t')*.trim().collect { col -> addMetadataPrefix(col) }

    def combos = lines.drop(1)
        .findAll { it -> it.trim() }
        .collect { line ->
            def row = line.split('\t', -1)*.trim()
            partition_columns.collectEntries { col ->
                def idx = headers.indexOf(col) // returns -1 if not found
                def value
                if (idx < 0) {
                    value = '_Absent_'
                } else {
                    value = idx < row.size() && row[idx] ? row[idx] : '_NA_'
                }
                [(col): value]
            }
        }
    return combos.unique()
}



//
// Generate methods description for MultiQC
//
def toolCitationText() {
    // TODO nf-core: Optionally add in-text citation tools to this list.
    // Can use ternary operators to dynamically construct based conditions, e.g. params["run_xyz"] ? "Tool (Foo et al. 2023)" : "",
    // Uncomment function in methodsDescriptionText to render in MultiQC report
    def citation_text = [
            "Tools used in the workflow included:",
            "."
        ].join(' ').trim()

    return citation_text
}

def toolBibliographyText() {
    // TODO nf-core: Optionally add bibliographic entries to this list.
    // Can use ternary operators to dynamically construct based conditions, e.g. params["run_xyz"] ? "<li>Author (2023) Pub name, Journal, DOI</li>" : "",
    // Uncomment function in methodsDescriptionText to render in MultiQC report
    def reference_text = [
        ].join(' ').trim()

    return reference_text
}

def methodsDescriptionText(mqc_methods_yaml) {
    // Convert  to a named map so can be used as with familiar NXF ${workflow} variable syntax in the MultiQC YML file
    def meta = [:]
    meta.workflow = workflow.toMap()
    meta["manifest_map"] = workflow.manifest.toMap()

    // Pipeline DOI
    if (meta.manifest_map.doi) {
        // Using a loop to handle multiple DOIs
        // Removing `https://doi.org/` to handle pipelines using DOIs vs DOI resolvers
        // Removing ` ` since the manifest.doi is a string and not a proper list
        def temp_doi_ref = ""
        def manifest_doi = meta.manifest_map.doi.tokenize(",")
        manifest_doi.each { doi_ref ->
            temp_doi_ref += "(doi: <a href=\'https://doi.org/${doi_ref.replace("https://doi.org/", "").replace(" ", "")}\'>${doi_ref.replace("https://doi.org/", "").replace(" ", "")}</a>), "
        }
        meta["doi_text"] = temp_doi_ref.substring(0, temp_doi_ref.length() - 2)
    } else meta["doi_text"] = ""
    meta["nodoi_text"] = meta.manifest_map.doi ? "" : "<li>If available, make sure to update the text to include the Zenodo DOI of version of the pipeline used. </li>"

    // Tool references
    meta["tool_citations"] = ""
    meta["tool_bibliography"] = ""

    // TODO nf-core: Only uncomment below if logic in toolCitationText/toolBibliographyText has been filled!
    // meta["tool_citations"] = toolCitationText().replaceAll(", \\.", ".").replaceAll("\\. \\.", ".").replaceAll(", \\.", ".")
    // meta["tool_bibliography"] = toolBibliographyText()


    def methods_text = mqc_methods_yaml.text

    def engine =  new groovy.text.SimpleTemplateEngine()
    def description_html = engine.createTemplate(methods_text).make(meta)

    return description_html.toString()
}
