//
// Subworkflow and functions for parsing, validating, and building analysis groups from the
// analysis groups file. See docs/analysis_groups_format.md for the file format.
//

/*
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    SUBWORKFLOW TO CREATE ANALYSIS GROUPS
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
*/

//
// Parse the analysis groups file, then build the lookup createGroupChannels uses to route partitions from every plate's
// plate partition keys (platemap partition column values plus project and plate_id), and check
// the analysis groups against those plate partition keys and for output tag collisions.
// Errors abort the run here: structural errors in the file are raised by parseGroupings while
// the workflow is being built, and the samplesheet is only emitted once the data checks pass,
// so no process is submitted with a bad groups file. Each platemap file is processed once.
//
workflow CREATE_ANALYSIS_GROUPS {
    take:
    ch_samplesheet       // channel: one validated samplesheet meta per plate
    groups_file          // string: path to the analysis groups file (null for a single group per aggregate level)
    platemap_well_column // string: name of the well column in the platemaps (excluded from plate_metadata_columns)

    main:
    def parsed_groups = parseGroupings(groups_file)
    def partition_columns = groupingPartitionColumns(parsed_groups)

    // the checks and group sizes need every plate at once, so collect the samplesheet into a single list
    def ch_checked = ch_samplesheet
        .toList()
        .map { metas ->
            // plates often share a platemap, so read each file only once.
            // Key by the path string: platemap_file may be a Path, String, or GString, and a GString
            // key won't match an equal String (or another GString) on lookup.
            // The original value is still what gets read, since an S3 Path's string drops the scheme.
            def platemaps = metas.groupBy { meta -> meta.platemap_file.toString() }.collectEntries { path_key, path_metas ->
                def platemap_file = path_metas[0].platemap_file
                [(path_key): [
                    partition_values: enumeratePlatemapPartitionValues(platemap_file, partition_columns),
                    // partition columns travel with meta even if a given platemap omits them
                    // (PYCYTOMINER_ANNOTATE fills any missing column with '_Absent_')
                    columns: (parsePlatemapColumns(platemap_file, platemap_well_column) + partition_columns).unique(),
                ]]
            }
            def plate_partition_keys = metas.collectMany { meta -> enumeratePlatePartitionKeys(meta, platemaps[meta.platemap_file.toString()].partition_values) }
            def analysis_groups = buildAnalysisGroups(parsed_groups, plate_partition_keys)
            missingGroupingColumns(parsed_groups, plate_partition_keys).each { warning -> log.warn("Analysis groups: ${warning}") }
            def errors = validateGroupingsAgainstData(parsed_groups, plate_partition_keys) + validateGroupTags(analysis_groups)
            if (errors) {
                error(
                    "Analysis groups validation failed:\n" + errors.collect { err -> "  - ${err}" }.join("\n")
                )
            }
            // add the platemap columns so they travel with meta through the pipeline
            def updated_metas = metas.collect { meta ->
                meta + [plate_metadata_columns: platemaps[meta.platemap_file.toString()].columns]
            }
            [updated_metas, analysis_groups]
        }

    // emitted only once the checks pass, with plate_metadata_columns added
    def ch_metas = ch_checked.flatMap { metas, _analysis_groups -> metas }

    // A value channel with the analysis groups lookup used to route partitions to groups:
    // partition_columns (passed to PARTITION_PROFILES; empty ⇒ no partitioning), and for each
    // of cell / well, partition key → list of [group_meta, expected group size].
    // See buildAnalysisGroups for the full structure and an example.
    def ch_analysis_groups = ch_checked.map { _metas, analysis_groups -> analysis_groups }

    emit:
    samplesheet     = ch_metas           // channel: one validated meta per plate
    analysis_groups = ch_analysis_groups // value channel: analysis groups lookup from buildAnalysisGroups
}

/*
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    FUNCTIONS
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
*/

//
// Ensure a column name has the "Metadata_" prefix.
//
def addMetadataPrefix(col) {
    return col.startsWith('Metadata_') ? col : 'Metadata_' + col
}

//
// Return the field separator for a platemap file, based on its extension:
// comma for .csv, tab for .tsv and .txt. Other extensions raise an error.
// pycytominer_annotate.py uses the same rule, so both read the file identically.
//
def platemapSeparator(platemap_path) {
    def extension = file(platemap_path).extension.toLowerCase()
    if (extension == 'csv') {
        return ','
    }
    if (extension in ['tsv', 'txt']) {
        return '\t'
    }
    throw new Exception("Platemap ${platemap_path} must have a .csv, .tsv, or .txt extension.")
}

//
// Read a platemap file (comma- or tab-delimited, see platemapSeparator) into a list of rows, one Map per well,
// keyed by Metadata_-prefixed column names, with whitespace trimmed from names and values.
// Columns with blank names and rows with only blank values are dropped.
// Quoted fields are supported, but escaped quotes (""), line breaks within quotes,
// and any characters (including spaces) after a closing quote raise an error.
// Duplicate column names (after prefixing), rows with more fields than the header,
// and platemaps with no data rows also raise an error.
//
def readPlatemap(platemap_path) {
    def platemap = file(platemap_path)
    if (!platemap.exists()) {
        throw new Exception("Platemap file not found: ${platemap_path}")
    }
    def sep = platemapSeparator(platemap_path)
    def lines = []
    try {
        lines = platemap.splitCsv(sep: sep, quote: '"')
    }
    catch (Exception e) {
        throw new Exception(
            "Could not parse platemap ${platemap_path}: ${e.message}\n" +
            "Check for unsupported quoting: escaped quotes (\"\"), line breaks within quotes, or any characters (including spaces) after a closing quote.",
            e
        )
    }

    // header row: blank names are kept as placeholders so column positions line up with fields
    def columns = (lines ? lines[0] : []).collect { col -> col.trim() ? addMetadataPrefix(col.trim()) : '' }
    def duplicates = columns
        .findAll { col -> col }
        .countBy { col -> col }
        .findAll { _col, count -> count > 1 }
        .keySet()
    if (duplicates) {
        throw new Exception("Platemap ${platemap_path} has duplicate column names: ${duplicates.join(', ')}")
    }

    // extra fields (even empty ones) would shift columns when read by pandas, so values would not match
    def data_lines = lines.drop(1)
    def overlong = data_lines.findAll { fields -> fields.size() > columns.size() }
    if (overlong) {
        throw new Exception(
            "Platemap ${platemap_path} has ${overlong.size()} row(s) with more fields than its ${columns.size()} header columns" +
            " (first starts with '${overlong[0][0]}')."
        )
    }

    def named_idx = columns.indices.findAll { idx -> columns[idx] }
    def rows = data_lines
        .collect { fields ->
            // fields[idx] is null for missing trailing fields
            named_idx.collectEntries { idx -> [(columns[idx]): fields[idx]?.trim() ?: ''] }
        }
        .findAll { row -> row.values().any() }
    if (!rows) {
        throw new Exception("Platemap ${platemap_path} has no data rows.")
    }
    return rows
}

//
// Check the structure of a parsed analysis groups file. See docs/analysis_groups_format.md.
// Each grouping is checked by validateGrouping; this function checks the file as a whole.
// Returns [errors: [...], warnings: [...]] so that every problem can be reported at once.
//
def validateGroupingStructure(parsed) {
    def errors = []
    def warnings = []
    def top_level_keys = ['default', 'cell', 'well']

    if (!(parsed instanceof Map)) {
        errors << "The analysis groups file is not the correct format; it should be a YAML or JSON file with top-level keys for 'default', 'cell', and/or 'well'."
        return [errors: errors, warnings: warnings]
    }

    // check that we only have the expected top-level keys
    (parsed.keySet().collect { key -> key as String } - top_level_keys).each { key ->
        errors << "Unknown top-level key '${key}': expected 'default', 'cell', and/or 'well'."
    }

    // check that each top-level key contains a list of groupings; a missing key has no groupings
    def groupings_by_key = top_level_keys.collectEntries { top_level_key ->
        def groupings = parsed.get(top_level_key)
        if (groupings != null && !(groupings instanceof List)) {
            errors << "'${top_level_key}' must contain a list of groupings."
            groupings = []
        }
        [(top_level_key): groupings ?: []]
    }

    // check each grouping
    groupings_by_key.each { top_level_key, groupings ->
        groupings.eachWithIndex { grouping, idx ->
            def result = validateGrouping(grouping, top_level_key, idx + 1)
            errors.addAll(result.errors)
            warnings.addAll(result.warnings)
        }
    }

    // check that grouping names are unique within each aggregate level, counting the default groupings added to it
    def named = groupings_by_key.collectEntries { top_level_key, groupings ->
        [(top_level_key): groupings.findAll { grouping -> grouping instanceof Map && grouping.name != null }.collect { grouping -> grouping.name as String }]
    }
    ['cell', 'well'].each { aggregate_level ->
        (named['default'] + named[aggregate_level]).countBy { name -> name }.findAll { _name, count -> count > 1 }.each { name, _count ->
            errors << "Grouping name '${name}' is used more than once in '${aggregate_level}' (including 'default' groupings)."
        }
    }

    return [errors: errors, warnings: warnings]
}


//
// Check the structure of a single grouping from the analysis groups file (see validateGroupingStructure).
// `position` is the grouping's 1-based position under its top-level key, used to label it in messages if it has no name.
// Returns [errors: [...], warnings: [...]].
//
def validateGrouping(grouping, top_level_key, position) {
    def errors = []
    def warnings = []

    if (!(grouping instanceof Map)) {
        errors << "Grouping ${position} in '${top_level_key}' ('${grouping}') must be a mapping with 'name', 'grouping_column', and 'groups' keys."
        return [errors: errors, warnings: warnings]
    }
    def label = grouping.name != null ? "Grouping '${grouping.name}' in '${top_level_key}'" : "Grouping ${position} in '${top_level_key}'"

    // check that all keys are known and the required keys are present
    def known_keys = ['name', 'grouping_column', 'groups', 'always_include', 'excluded', 'filter', 'subgroup_columns']
    (grouping.keySet().collect { key -> key as String } - known_keys).each { key ->
        errors << "${label}: unknown key '${key}'."
    }
    ['name', 'grouping_column', 'groups'].findAll { key -> grouping.get(key) == null }.each { key ->
        errors << "${label}: missing required key '${key}'."
    }

    // check that the name can be used in output file names
    if (grouping.name != null && !((grouping.name as String) ==~ /[A-Za-z0-9_-]+/)) {
        errors << "${label}: name may contain only letters, digits, '_', and '-'."
    }

    // check that grouping_column is a single column name
    def grouping_column = null
    if (grouping.grouping_column != null) {
        if (!(grouping.grouping_column instanceof String) || (grouping.grouping_column as String).contains(',')) {
            errors << "${label}: grouping_column must be a single column name; use subgroup_columns to subdivide by other columns."
        }
        else {
            // add the metadata prefix here so the conflict checks below can compare column names consistently
            grouping_column = addMetadataPrefix(grouping.grouping_column as String)
        }
    }

    // check that groups is one of the special strings, or a mapping of group names to lists of values
    def groups = grouping.groups
    if (groups instanceof Map) {
        if (groups.isEmpty()) {
            errors << "${label}: groups mapping must define at least one group."
        }
        groups.each { group_name, values ->
            if (!isFlatList(values)) {
                errors << "${label}: group '${group_name}' must be a list of values, without nested lists or mappings."
            }
        }
    }
    else if (groups != null && !(groups in ['one_per_value', 'all_values'])) {
        errors << "${label}: groups must be 'one_per_value', 'all_values', or a mapping of group names to lists of values."
    }

    // check that the optional keys are the right type, if present
    ['always_include', 'excluded', 'subgroup_columns'].each { key ->
        if (grouping.get(key) != null && !isFlatList(grouping.get(key))) {
            errors << "${label}: ${key} must be a list, without nested lists or mappings."
        }
    }
    if (grouping.filter != null) {
        if (!(grouping.filter instanceof Map)) {
            errors << "${label}: filter must be a mapping of column names to lists of values."
        }
        else {
            grouping.filter.each { col, values ->
                if (!isFlatList(values)) {
                    errors << "${label}: filter column '${col}' must be a list of values, without nested lists or mappings."
                }
            }
        }
    }

    // check that filter and subgroup_columns don't name the grouping_column
    if (grouping_column) {
        if (grouping.filter instanceof Map && grouping.filter.keySet().any { col -> addMetadataPrefix(col as String) == grouping_column }) {
            errors << "${label}: filter must not name the grouping_column; use excluded to drop its values."
        }
        if (grouping.subgroup_columns instanceof List && grouping.subgroup_columns.any { col -> addMetadataPrefix(col as String) == grouping_column }) {
            errors << "${label}: subgroup_columns must not name the grouping_column."
        }
    }

    // check that always_include values aren't also excluded or used to define a named group
    if (grouping.always_include instanceof List) {
        def always_include = toStringList(grouping.always_include)
        if (grouping.excluded instanceof List) {
            def overlap = always_include.intersect(toStringList(grouping.excluded))
            if (overlap) {
                errors << "${label}: values ${overlap} are in both always_include and excluded."
            }
        }
        // always_include values are in every group, so they can't also define a named group
        // (a group of only those values would have no members of its own and be dropped)
        if (groups instanceof Map) {
            def group_values = toStringList(groups.values().findAll { values -> values instanceof List }.flatten())
            def overlap = group_values.intersect(always_include).unique()
            if (overlap) {
                def overlapping_groups = groups.findAll { _group_name, values ->
                    values instanceof List && values.any { value -> (value as String) in overlap }
                }
                errors << ("${label}: values ${overlap} are in both always_include and named groups ${overlapping_groups.keySet() as List}." +
                    " always_include values are added to every group; for a standalone group of these values, add a separate grouping entry.")
            }
        }
    }
    if (groups == 'all_values' && grouping.always_include) {
        warnings << "${label}: always_include has no effect with 'groups: all_values'."
    }

    return [errors: errors, warnings: warnings]
}

//
// Check if values are a flat list (no nested lists or mappings).
//
def isFlatList(values) {
    return values instanceof List && !values.any { value -> value instanceof List || value instanceof Map }
}

//
// Normalize a structurally valid parsed analysis groups file into a map of
// aggregate_level ('cell'/'well') → list of groupings. Each grouping has every key filled in:
//   [name, grouping_column, groups, always_include, excluded, filter, subgroup_columns]
// Column names get the Metadata_ prefix, and all values are coerced to strings to match
// the values read from platemaps. 'default' groupings are placed first in each aggregate level.
//
def normalizeGroupings(parsed) {
    def default_groupings = (parsed.get('default') ?: []).collect { grouping -> normalizeGrouping(grouping) }
    return ['cell', 'well'].collectEntries { aggregate_level ->
        [(aggregate_level): default_groupings + (parsed.get(aggregate_level) ?: []).collect { grouping -> normalizeGrouping(grouping) }]
    }
}

//
// Normalize a single grouping; see normalizeGroupings.
//
def normalizeGrouping(grouping) {
    def groups = grouping.groups instanceof Map
        ? grouping.groups.collectEntries { group_name, values -> [(group_name as String): toStringList(values)] }
        : grouping.groups as String
    return [
        name: grouping.name as String,
        grouping_column: addMetadataPrefix(grouping.grouping_column as String),
        groups: groups,
        always_include: toStringList(grouping.always_include),
        excluded: toStringList(grouping.excluded),
        filter: (grouping.filter ?: [:]).collectEntries { col, values -> [(addMetadataPrefix(col as String)): toStringList(values)] },
        subgroup_columns: (grouping.subgroup_columns ?: []).collect { col -> addMetadataPrefix(col as String) },
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
// normalized groupings (see normalizeGroupings and docs/analysis_groups_format.md).
// Structural errors abort the run here, before any process is submitted.
// If no file is given, each aggregate level gets a single grouping named 'all' with no grouping column,
// meaning "one all-encompassing group per aggregate level" (no partitioning).
//
def parseGroupings(groups_file) {
    if (!groups_file) {
        def all_grouping = [
            name: 'all',
            grouping_column: null,
            groups: 'all_values',
            always_include: [],
            excluded: [],
            filter: [:],
            subgroup_columns: [],
        ]
        return [cell: [all_grouping], well: [all_grouping]]
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
// Columns PARTITION_PROFILES must split by: the union of every grouping's grouping_column,
// subgroup_columns, and filter columns across both aggregate levels.
//
def groupingPartitionColumns(parsed_groups) {
    def groupings = parsed_groups.cell + parsed_groups.well
    def columns = groupings.collectMany { grouping ->
        [grouping.grouping_column] + grouping.subgroup_columns + grouping.filter.keySet()
    }
    return columns.findAll().unique() as List<String>
}

//
// Combine the normalized analysis groups with the plate partition keys of every plate (from
// enumeratePlatePartitionKeys) to produce the lookup createGroupChannels uses to route partitions:
//
//   [
//     partition_columns: ['Metadata_treatment', ...],
//     cell: [ (partition_key): [[group_meta, expected_size], ...], ... ],
//     well: [ ... ],
//   ]
//
// A partition_key (see partitionKey) is a plate partition key's partition column values plus project
// (i.e., without plate_id), which is what each partition's meta carries after expandHivePartitions. Every observed partition key is
// present at both aggregate levels; an empty list means the partition belongs to no group at that
// aggregate level (e.g., it was filtered out or excluded).
//
// group_meta identifies the group:
//   [project, aggregate_level, grouping_name, grouping_column, group, subgroup_values, group_id, group_tag]
// group_tag (see groupTag) names the group's output files.
// Expected sizes count the plate partition keys in each group. They're used as the `size`
// argument to groupKey(...) so groupTuple can emit each group eagerly rather than waiting for
// the upstream channel to close.
//
// Sizes are upper bounds: COSMICQC_FILTER may drop wells, in which case the corresponding
// partition won't materialise and that group falls back to channel-close emission via
// remainder: true.
//
// Example (one plate, one grouping by treatment with DMSO always included):
//
//   [
//     partition_columns: ['Metadata_treatment'],
//     cell: [
//       [Metadata_treatment: 'drugA', project: 'cpg0016']: [[drugA_meta, 2]],
//       [Metadata_treatment: 'drugB', project: 'cpg0016']: [[drugB_meta, 2]],
//       [Metadata_treatment: 'DMSO', project: 'cpg0016']: [[drugA_meta, 2], [drugB_meta, 2]],
//     ],
//     well: [...]
//   ]
//
// where drugA_meta is
//   [project: 'cpg0016', aggregate_level: 'cell', grouping_name: 'by_treatment',
//    grouping_column: 'Metadata_treatment', group: 'drugA', subgroup_values: [:],
//    group_id: 'treatment=drugA', group_tag: 'cell__by_treatment__treatment=drugA']
//
def buildAnalysisGroups(parsed_groups, plate_partition_keys) {
    def partition_columns = groupingPartitionColumns(parsed_groups)
    // LinkedHashSet dedups by hash (unique() is quadratic) while keeping first-seen order
    def partition_keys = plate_partition_keys.collect { plate_partition_key -> partitionKey(plate_partition_key, partition_columns) } as LinkedHashSet

    def aggregate_level_lookups = ['cell', 'well'].collectEntries { aggregate_level ->
        def lookup = partition_keys.collectEntries { partition_key -> [(partition_key): []] }
        parsed_groups[aggregate_level].each { grouping ->
            assignGroupingSets(grouping, plate_partition_keys).each { group_key, members ->
                def group_meta = [
                    project: group_key.project,
                    aggregate_level: aggregate_level,
                    grouping_name: grouping.name,
                    grouping_column: grouping.grouping_column,
                    group: group_key.group,
                    subgroup_values: group_key.subgroup_values,
                    group_id: groupId(grouping.grouping_column, group_key.group, group_key.subgroup_values),
                ]
                group_meta = group_meta + [group_tag: groupTag(group_meta)]
                def group_with_size = [group_meta, members.size()]
                // the same partition key can come from several plates; list each group once
                def member_keys = members.collect { plate_partition_key -> partitionKey(plate_partition_key, partition_columns) } as Set
                member_keys.each { partition_key -> lookup[partition_key] << group_with_size }
            }
        }
        [(aggregate_level): lookup]
    }
    return [partition_columns: partition_columns] + aggregate_level_lookups
}

//
// The key that routes a partition (or plate partition key) to its groups: its partition column values plus
// project. Used both to build the analysis groups lookup and to look partitions up in it.
//
def partitionKey(meta, partition_columns) {
    return meta.subMap(partition_columns + ['project'])
}

//
// Assign plate partition keys to the grouping sets (groups) of a single normalized grouping. This is the single
// definition of group membership (see docs/analysis_groups_format.md):
//   1. keep only plate_partition_keys matching every `filter` column
//   2. drop plate_partition_keys whose grouping_column value is in `excluded`
//   3. assign the rest to groups by value: the value itself (one_per_value), 'all_values',
//      or every named group listing the value (named groups may overlap)
//   4. add `always_include` plate_partition_keys to every group with the same project and subgroup values;
//      they don't form groups of their own (except with all_values, where they are ordinary)
// Returns a map of group key [project, group, subgroup_values] → list of member plate_partition_keys.
//
def assignGroupingSets(grouping, plate_partition_keys) {
    def (shared, core) = splitGroupingPlatePartitionKeys(grouping, plate_partition_keys)

    def groups = [:]
    core.each { plate_partition_key ->
        def value = plate_partition_key[grouping.grouping_column]
        def group_names
        if (grouping.groups == 'one_per_value') {
            group_names = [value]
        }
        else if (grouping.groups == 'all_values') {
            group_names = ['all_values']
        }
        else {
            group_names = grouping.groups.findAll { _group_name, values -> value in values }.keySet() as List
        }
        group_names.each { group_name ->
            def group_key = [project: plate_partition_key.project, group: group_name, subgroup_values: plate_partition_key.subMap(grouping.subgroup_columns)]
            groups.get(group_key, []) << plate_partition_key
        }
    }
    shared.each { plate_partition_key ->
        def subgroup_values = plate_partition_key.subMap(grouping.subgroup_columns)
        groups.findAll { group_key, _members -> group_key.project == plate_partition_key.project && group_key.subgroup_values == subgroup_values }
            .each { _group_key, members -> members << plate_partition_key }
    }
    return groups
}

//
// Apply steps 1 and 2 of assignGroupingSets (filter, then drop excluded values) and split the
// remaining plate_partition_keys into [shared, core]: shared plate_partition_keys have an always_include value and join
// every group; core plate_partition_keys define the groups. With all_values every plate_partition_key is core.
//
def splitGroupingPlatePartitionKeys(grouping, plate_partition_keys) {
    def always_included = grouping.groups == 'all_values' ? [] : grouping.always_include
    return plate_partition_keys
        .findAll { plate_partition_key -> matchesGroupingFilter(grouping, plate_partition_key) && !(plate_partition_key[grouping.grouping_column] in grouping.excluded) }
        .split { plate_partition_key -> plate_partition_key[grouping.grouping_column] in always_included }
}

//
// Whether a plate_partition_key passes a grouping's filter: its value must be one of the listed values
// for every filter column.
//
def matchesGroupingFilter(grouping, plate_partition_key) {
    return grouping.filter.every { col, values -> plate_partition_key[col] in values }
}

//
// Build a group id from the grouping column, group name, and subgroup values, e.g.
//   'treatment=kinase_inhibitor__cell_type=lineA'
// Column names are shown without the Metadata_ prefix. With no grouping column (no analysis
// groups file), the id is 'all'.
//
def groupId(grouping_column, group, subgroup_values) {
    if (!grouping_column) {
        return 'all'
    }
    def parts = [(grouping_column): group] + subgroup_values
    return parts.collect { col, value -> "${(col as String).replaceFirst('^Metadata_', '')}=${value}" }.join('__')
}

//
// Build a filesystem-safe tag from a group meta, used to name the group's output files:
//   <aggregate_level>__<grouping_name>__<group_id>
// e.g. cell__moa_by_cell_line__treatment=kinase_inhibitor__cell_type=lineA
// When there is no grouping column (the 'all' grouping used without an analysis groups file),
// there is only one group, so the group id is left out: "<aggregate_level>__all".
//
def groupTag(group_meta) {
    def tag = group_meta.grouping_column
        ? "${group_meta.aggregate_level}__${group_meta.grouping_name}__${group_meta.group_id}"
        : "${group_meta.aggregate_level}__${group_meta.grouping_name}"
    return tag.replaceAll('[^A-Za-z0-9._=-]', '_')
}

//
// Check that distinct groups get distinct output tags within each project and aggregate level,
// since published outputs are only separated by project, aggregate level, and tag.
// groupTag replaces characters outside [A-Za-z0-9._=-] with '_', so groups or values that differ
// only in those characters (e.g. 'kinase inhibitor' and 'kinase/inhibitor') would otherwise
// overwrite each other's outputs. Takes the lookup from buildAnalysisGroups and returns a list
// of error messages.
//
def validateGroupTags(analysis_groups) {
    def group_metas = ['cell', 'well']
        .collectMany { aggregate_level ->
            (analysis_groups[aggregate_level] ?: [:]).values().collectMany { groups ->
                groups.collect { group_meta, _size -> group_meta }
            }
        }
        .unique()
    return group_metas
        .groupBy { group_meta -> [group_meta.project, group_meta.aggregate_level, group_meta.group_tag] }
        .findAll { _key, metas -> metas.size() > 1 }
        .collect { key, metas ->
            def names = metas.collect { group_meta -> group_meta.grouping_name }.unique().sort()
            def ids = metas.collect { group_meta -> group_meta.group_id }.sort()
            ("Grouping '${names.join("', '")}': groups ${ids} in project '${key[0]}'" +
                " would all be written with the output tag '${key[2]}'." +
                " Output tags keep only letters, digits, '.', '_', '=', and '-'," +
                " so rename groups or values that differ only in other characters.").toString()
        }
}

//
// Check the analysis groups against the plate partition keys observed in the platemaps
// Returns a list of error messages:
//   - a filter column missing from any plate's platemap
//   - a filter that matches no rows
//   - named groups that don't account for an observed (filtered) value
//   - always_include values absent from the filtered rows
//   - always_include values absent from any subgroup (within a project) that has other rows
// Groupings shared by both aggregate levels (default groupings) are checked once.
//
def validateGroupingsAgainstData(parsed_groups, plate_partition_keys) {
    def errors = []
    def groupings = (parsed_groups.cell + parsed_groups.well).unique()
    groupings.findAll { grouping -> grouping.grouping_column }.each { grouping ->
        def label = "Grouping '${grouping.name}'"

        // filter columns must be present in every platemap, or those plates silently drop out
        grouping.filter.keySet().each { col ->
            def plates = plate_partition_keys.findAll { plate_partition_key -> plate_partition_key[col] == '_Absent_' }.collect { plate_partition_key -> plate_partition_key.plate_id }.unique()
            if (plates) {
                errors << "${label}: filter column '${col}' is missing from the platemap for plate(s) ${plates.join(', ')}."
            }
        }

        def filtered = plate_partition_keys.findAll { plate_partition_key -> matchesGroupingFilter(grouping, plate_partition_key) }
        if (grouping.filter && !filtered) {
            errors << "${label}: filter ${grouping.filter} matches no rows."
            return
        }
        def observed = filtered.collect { plate_partition_key -> plate_partition_key[grouping.grouping_column] }.unique()

        // accounting rule for named groups
        if (grouping.groups instanceof Map) {
            def accounted = grouping.groups.values().flatten() + grouping.always_include + grouping.excluded
            def unaccounted = observed.findAll { value -> !(value in accounted) }
            if (unaccounted) {
                errors << "${label}: values ${unaccounted} of '${grouping.grouping_column}' are not in any group, always_include, or excluded."
            }
        }

        if (grouping.groups == 'all_values' || !grouping.always_include) {
            return
        }
        def missing = grouping.always_include.findAll { value -> !(value in observed) }
        if (missing) {
            def where = grouping.filter ? " after applying filter ${grouping.filter}" : ''
            errors << "${label}: always_include values ${missing} were not found in '${grouping.grouping_column}'${where}."
        }

        // every subgroup (within a project) with other rows needs the always_include values
        def present = grouping.always_include - missing
        def subgroupOf = { plate_partition_key -> [project: plate_partition_key.project] + plate_partition_key.subMap(grouping.subgroup_columns) }
        def subgroup_values = filtered.groupBy(subgroupOf).collectEntries { subgroup, subgroup_plate_partition_keys ->
            [(subgroup): subgroup_plate_partition_keys.collect { plate_partition_key -> plate_partition_key[grouping.grouping_column] } as Set]
        }
        def (_shared, core) = splitGroupingPlatePartitionKeys(grouping, filtered)
        core.collect(subgroupOf).unique().each { subgroup ->
            def subgroup_missing = present.findAll { value -> !(value in subgroup_values[subgroup]) }
            if (subgroup_missing) {
                errors << "${label}: always_include values ${subgroup_missing} were not found in subgroup ${subgroup}."
            }
        }
    }
    return errors
}

//
// Find grouping and subgroup columns missing from the platemaps of plates a grouping uses
// (after its filter). Not an error, since a column may be absent from only some plates,
// but a typo would otherwise silently put every row in a single '_Absent_' group.
// Returns a list of warning messages; groupings shared by both aggregate levels are checked once.
//
def missingGroupingColumns(parsed_groups, plate_partition_keys) {
    def groupings = (parsed_groups.cell + parsed_groups.well).unique()
    return groupings.findAll { grouping -> grouping.grouping_column }.collectMany { grouping ->
        def filtered = plate_partition_keys.findAll { plate_partition_key -> matchesGroupingFilter(grouping, plate_partition_key) }
        ([grouping.grouping_column] + grouping.subgroup_columns).findResults { col ->
            def plates = filtered.findAll { plate_partition_key -> plate_partition_key[col] == '_Absent_' }.collect { plate_partition_key -> plate_partition_key.plate_id }.unique()
            plates
                ? "Grouping '${grouping.name}': column '${col}' is missing from the platemap for plate(s) ${plates.join(', ')}.".toString()
                : null
        }
    }
}

//
// List the plate partition keys for one plate: each unique combination of partition column values
// in its platemap (platemap_partition_values, from enumeratePlatemapPartitionValues), plus project and plate_id.
// A plate partition key is a partition key plus plate_id, without an aggregate level;
// each one defines the cell and well partitions a plate can produce.
// Plate-level metadata from the samplesheet (not yet supported) would be merged in here.
//
def enumeratePlatePartitionKeys(meta, platemap_partition_values) {
    return platemap_partition_values
        .collect { plate_partition_key -> plate_partition_key + [project: meta.project, plate_id: meta.plate_id] }
}

//
// Parse the columns from a platemap file, excluding the well column.
// Returns a list of column names with "Metadata_" prefix added if not already present.
// The platemap is read with readPlatemap, so its checks apply; a missing well column is also an error.
//
def parsePlatemapColumns(platemap_path, platemap_well_column) {
    // every row has the same keys: the (Metadata_-prefixed) platemap columns
    def rows = readPlatemap(platemap_path)
    def columns = rows[0].keySet() as List
    def well_column = addMetadataPrefix(platemap_well_column)
    if (!(well_column in columns)) {
        throw new Exception(
            "Platemap ${platemap_path} is missing the well column '${platemap_well_column}' (set by --platemap_well_column)."
        )
    }
    return columns.findAll { col -> col != well_column }
}

//
// Read a platemap file and return the list of unique partition column value combinations present on the plate.
// Each combination is a Map keyed by Metadata_-prefixed column names → row values, read the same
// way pycytominer_annotate.py reads the platemap before partitioning (see readPlatemap), and
//   - blank cell                                 → '_NA_'
//   - partition column absent from this platemap → '_Absent_' (mirrors the plate_metadata_columns fill)
// Keeping the encoding aligned is what lets buildAnalysisGroups predict partition keys that
// actually match the values recorded by PARTITION_PROFILES.
//
def enumeratePlatemapPartitionValues(platemap_path, partition_columns) {
    def rows = readPlatemap(platemap_path)

    // every row has the same keys, so the platemap's columns can be read from the first row
    def present_columns = rows[0].keySet()
    def partition_values = rows.collect { row ->
        partition_columns.collectEntries { col ->
            [(col): col in present_columns ? (row[col] ?: '_NA_') : '_Absent_']
        }
    }
    return partition_values.unique()
}
