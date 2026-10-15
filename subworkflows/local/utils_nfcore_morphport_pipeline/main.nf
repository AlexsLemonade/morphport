//
// Subworkflow with functionality specific to the AlexsLemonade/morphport pipeline
//

/*
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    IMPORT FUNCTIONS / MODULES / SUBWORKFLOWS
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
*/

include { UTILS_NFSCHEMA_PLUGIN        } from '../../nf-core/utils_nfschema_plugin'
include { paramsSummaryMap             } from 'plugin/nf-schema'
include { samplesheetToList            } from 'plugin/nf-schema'
include { paramsHelp                   } from 'plugin/nf-schema'
include { completionEmail              } from '../../nf-core/utils_nfcore_pipeline'
include { completionSummary            } from '../../nf-core/utils_nfcore_pipeline'
include { UTILS_NFCORE_PIPELINE        } from '../../nf-core/utils_nfcore_pipeline'
include { UTILS_NEXTFLOW_PIPELINE      } from '../../nf-core/utils_nextflow_pipeline'
include { CREATE_ANALYSIS_GROUPS       } from '../create_analysis_groups'
include { addMetadataPrefix            } from '../create_analysis_groups'
include { readPlatemap                 } from '../create_analysis_groups'
include { parseGroupings               } from '../create_analysis_groups'
include { groupingPartitionColumns     } from '../create_analysis_groups'

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

    //
    // Read and validate the samplesheet, then check the analysis groups against every plate's
    // platemap and build the analysis groups lookup (see CREATE_ANALYSIS_GROUPS).
    // Errors in either abort the run before any process is submitted.
    //
    def ch_metas = channel
        .fromList(samplesheetToList(input, "${projectDir}/assets/schema_input.json"))
        .map { row -> row[0] }
        .toList()
        .map { samplesheet -> validateInputSamplesheet(samplesheet, partition_columns) }

    CREATE_ANALYSIS_GROUPS(ch_metas, parsed_groups)

    emit:
    samplesheet     = CREATE_ANALYSIS_GROUPS.out.samplesheet
    analysis_groups = CREATE_ANALYSIS_GROUPS.out.analysis_groups
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
// Parse the columns from a platemap file, excluding the well column (which annotation will replace with Metadata_Well)
// Returns a list of column names with "Metadata_" prefix added if not already present.
// `extra_columns` (already Metadata_-prefixed) are appended so that columns required
// downstream, like the partition columns, travel with meta even if a given platemap
// happens to omit them.
//
def parsePlatemapColumns(platemap_path, platemap_well_column, extra_columns = []) {
    // read only the first well; its keys are the (Metadata_-prefixed) platemap columns
    def rows = readPlatemap(platemap_path, 1)
    def well_column = addMetadataPrefix(platemap_well_column)
    def header_cols = (rows ? rows[0].keySet() as List : [])
        .findAll { col -> col && col != well_column }
    return (header_cols + extra_columns).unique()
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
