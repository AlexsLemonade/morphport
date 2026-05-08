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
        command
    )

    //
    // Check config provided to the pipeline
    //
    UTILS_NFCORE_PIPELINE (
        nextflow_cli_args
    )

    //
    // Create channel from input file provided through params.input
    //

    channel
        .fromList(samplesheetToList(input, "${projectDir}/assets/schema_input.json"))
        .map { row -> row[0] }
        .toList()
        .map { samplesheet ->
            validateInputSamplesheet(samplesheet)
        }
        .flatMap()
        .set { ch_samplesheet }

    //
    // Build a nested map of analysis groupings → sub-combo → expected size, derived from the
    // user's analysis_groups_file and the union of condition combinations across each plate's
    // platemap. This is a single value channel that carries both the grouping structure (its
    // keys) and the predicted sizes (its leaves), used downstream to fan out partitions and
    // wrap them with groupKey(...).
    //
    def parsed_groups = parseGroupings(params.analysis_groups_file, params.platemap_condition_columns)
    def ch_analysis_groups = ch_samplesheet
        .map { meta -> parsePlatemapCombos(meta.platemap_file, params.platemap_condition_columns) }
        .collect()
        .map { per_plate -> buildAnalysisGroups(parsed_groups, per_plate.flatten() as List) }

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
def validateInputSamplesheet(metas) {
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
        // condition columns are always included even if absent from this platemap
        def platemap_cols = platemap_path ? parsePlatemapColumns(platemap_path, params.platemap_well_column, params.platemap_condition_columns) : []

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
String addMetadataPrefix(String col) {
    return col.startsWith('Metadata_') ? col : 'Metadata_' + col
}

//
// Apply addMetadataPrefix to every column name in every grouping in a list.
//
List normaliseGroupings(List groupings_list) {
    return groupings_list.collect { cols -> cols.collect { col -> addMetadataPrefix(col as String) } }
}

//
// Parse analysis_groups_file into a Map keyed by aggregate_level ('cell'/'well'), where each
// value is a list of groupings and each grouping is a list of Metadata_-prefixed column names.
// Falls back to one single-column grouping per condition column when no file is provided.
//
Map parseGroupings(String groups_file, String condition_columns_str) {
    def default_groupings = condition_columns_str.split(',')
        .collect { col -> [addMetadataPrefix(col.trim())] }

    if (!groups_file) {
        return [cell: default_groupings, well: default_groupings]
    }

    def parsed = new groovy.json.JsonSlurper().parse(file(groups_file)) as Map
    return [
        cell: parsed.containsKey('cell') ? normaliseGroupings(parsed['cell'] as List) : [],
        well: parsed.containsKey('well') ? normaliseGroupings(parsed['well'] as List) : [],
    ]
}

//
// Parse the columns from a platemap file, excluding the well column.
// Returns a list of column names with "Metadata_" prefix added if not already present.
//
def parsePlatemapColumns(platemap_path, platemap_well_column, condition_columns_str = '') {
    // read only the header line of the platemap file
    def text = file(platemap_path).newReader().readLine()
    // strip UTF-8 BOM if present
    if (text.startsWith('\uFEFF')) text = text.substring(1)

    def cols = (text.split('\t') + condition_columns_str.split(','))
        .collect { it -> it.trim() }
        .findAll { it -> it != platemap_well_column }
        .collect { it -> it.startsWith('Metadata_') ? it : "Metadata_${it}" }
    return cols.unique()
}

//
// Combine parsed analysis-group definitions with the union of platemap condition combos to
// produce a nested map: aggregate_level → grouping_cols → sub_combo → expected size.
//
// The structure carries both the grouping definitions (its keys) and the predicted group
// sizes (its leaves). Sizes count, for each (level, grouping_cols, sub_combo) triple, the
// number of (plate × full_combo) pairs whose projection onto grouping_cols equals sub_combo.
// They're used as the `size` argument to groupKey(...) so groupTuple can emit each group
// eagerly rather than waiting for the upstream channel to close.
//
// Sizes are upper bounds: COSMICQC_FILTER may drop wells, in which case the corresponding
// partition won't materialise and that group falls back to channel-close emission via
// remainder: true.
//
Map buildAnalysisGroups(Map groupings, List all_combos) {
    def result = [:]
    groupings.each { agg_level, level_groupings ->
        def level_map = [:]
        level_groupings.each { grouping_cols ->
            def subcombo_sizes = [:]
            all_combos.each { full_combo ->
                def sub = full_combo.subMap(grouping_cols)
                subcombo_sizes[sub] = (subcombo_sizes[sub] ?: 0) + 1
            }
            level_map[grouping_cols] = subcombo_sizes
        }
        result[agg_level] = level_map
    }
    return result
}

//
// Read a platemap file and return the list of unique condition combinations present on the plate.
// Each combination is a Map keyed by Metadata_-prefixed column names \u2192 row values, with the same
// sentinel encoding that pycytominer_annotate.py applies before partitioning:
//   - blank cell                                 \u2192 '_NA_'    (mirrors fillna("_NA_"))
//   - condition column absent from this platemap \u2192 '_Absent_' (mirrors the plate_metadata_columns fill)
// Keeping the encoding aligned is what lets buildAnalysisGroups predict sizes that actually match
// the keys produced by expandHivePartitions.
//
def parsePlatemapCombos(platemap_path, condition_columns_str) {
    def lines = file(platemap_path).text.readLines()
    if (lines.size() < 2) return []

    def header_line = lines[0]
    if (header_line.startsWith('\uFEFF')) header_line = header_line.substring(1)
    def headers = header_line.split('\t')*.trim()

    def cond_cols = condition_columns_str.split(',')*.trim().findAll { it -> it }
    def col_indices = cond_cols.collectEntries { col ->
        def metadata_key = col.startsWith('Metadata_') ? col : 'Metadata_' + col
        // strip the prefix to look up the raw column in the platemap header
        def raw = col.startsWith('Metadata_') ? col.substring('Metadata_'.length()) : col
        [(metadata_key): headers.indexOf(raw)]
    }

    def combos = [] as LinkedHashSet
    lines.drop(1).each { line ->
        if (!line.trim()) return
        def parts = line.split('\t', -1)*.trim()
        def combo = col_indices.collectEntries { metadata_key, idx ->
            def value
            if (idx < 0) {
                value = '_Absent_'
            }
            else {
                def raw_value = (idx < parts.size()) ? parts[idx] : ''
                value = raw_value ?: '_NA_'
            }
            [(metadata_key): value]
        }
        combos << combo
    }
    return combos.toList()
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
