/*
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    IMPORT MODULES / SUBWORKFLOWS / FUNCTIONS
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
*/
include { paramsSummaryMap       } from 'plugin/nf-schema'
include { softwareVersionsToYAML } from '../subworkflows/nf-core/utils_nfcore_pipeline'
include { methodsDescriptionText } from '../subworkflows/local/utils_nfcore_pccma_pipeline'
include { CYTOTABLE_CONVERT      } from '../modules/local/cytotable_convert'

/*
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    RUN MAIN WORKFLOW
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
*/

workflow PCCMA {
    take:
    ch_samplesheet // channel: samplesheet read in from --input

    main:

    ch_validated = validatePlateDirs(ch_samplesheet)
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

/*
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    FUNCTIONS
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
*/

def validatePlateDirs(ch) {
    // function to validate that we have plate directories for all samples,
    // and that they contain the expected files for downstream steps
    //based on the cytotable conversion preset
    def metas = ch.toList().get()
    def errors = []

    def processed = metas.collect { meta ->
        def plate_dir = meta.plate_dir
        if (!plate_dir) {
            if (meta.project =~ /^cpg\d{4}/) {
                plate_dir = "s3://cellpainting-gallery/${meta.project}/${meta.source}/workspace/backend/${meta.batch}/${meta.plate_id}"
            }
            else {
                errors << "Plate '${meta.plate_id}': project '${meta.project}'" + " does not match the expected CPG format (e.g. cpg0016-jump)." + " Provide 'plate_dir' explicitly in the samplesheet."
            }
        }
        meta + [plate_dir: plate_dir ?: ""]
    }

    processed.each { meta ->
        if (meta.plate_dir && meta.cytotable_preset.contains("sqlite")) {
            def sqlite_files = file("${meta.plate_dir}/*.sqlite")
            if (sqlite_files.isEmpty()) {
                errors << "No sqlite file found for plate '${meta.plate_id}' at ${meta.plate_dir}"
            }
        }
        else if (meta.plate_dir && meta.cytotable_preset.contains("csv")) {
            // check for expected CSV files
            def expected_csvs = ["Cells.csv", "Cytoplasm.csv", "Nuclei.csv", "Image.csv"]
            def missing = expected_csvs.findAll { csv_file -> !file("${meta.plate_dir}/${csv_file}").exists() }
            if (missing) {
                errors << "Expected CSV files not found for plate '${meta.plate_id}' at ${meta.plate_dir}: ${missing.join(', ')}"
            }
        }
        else if (meta.plate_dir && meta.cytotable_preset == "skip") {
            // skip cytotable conversion, but check that we have one parquet file for later steps
            def parquet_files = file("${meta.plate_dir}/*.parquet")
            if (parquet_files.isEmpty()) {
                errors << "No parquet file found for plate '${meta.plate_id}' at ${meta.plate_dir}"
            }
            else if (parquet_files.size() > 1) {
                errors << "Multiple parquet files found for plate '${meta.plate_id}' at ${meta.plate_dir}: ${parquet_files.collect { it.name }.join(', ')}"
            }
        }
    }

    if (errors) {
        log.error(
            "Samplesheet validation failed:\n" + errors.collect { err -> "  - ${err}" }.join("\n")
        )
        System.exit(1)
    }

    return channel.fromList(processed)
}
