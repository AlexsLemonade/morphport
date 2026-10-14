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
// Check the analysis groups against every plate's condition combos (platemap values plus
// project and plate_id), then build the lookup createGroupChannels uses to route partitions.
// Errors abort the run here: the samplesheet is only emitted once the checks pass, so no
// process is submitted with a bad groups file. Platemaps are read only once.
//
workflow CREATE_ANALYSIS_GROUPS {
    take:
    ch_metas      // value channel: list of validated samplesheet metas, one per plate
    parsed_groups // map: normalized analysis groups from parseGroupings

    main:
    def partition_columns = groupingPartitionColumns(parsed_groups)

    def ch_checked = ch_metas
        .map { metas ->
            def combos = metas.collectMany { meta -> enumeratePlateCombos(meta, partition_columns) }
            def errors = validateGroupingsAgainstData(parsed_groups, combos)
            if (errors) {
                error(
                    "Analysis groups validation failed:\n" + errors.collect { err -> "  - ${err}" }.join("\n")
                )
            }
            [metas, combos]
        }

    def ch_samplesheet = ch_checked.flatMap { metas, _combos -> metas }

    //
    // Build a value channel with the analysis groups lookup used to route partitions to groups
    // (see buildAnalysisGroups for details):
    //   - partition_columns: list passed to PARTITION_CONDITIONS (if list is empty ⇒ no partitioning)
    //   - cell / well:       partition key → list of [group_meta, expected group size], used to
    //                        wrap downstream items with groupKey(...) for eager groupTuple emit.
    //
    // Example output structure (one plate, one grouping by treatment with DMSO always included):
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
    def ch_analysis_groups = ch_checked
        .map { _metas, combos -> buildAnalysisGroups(parsed_groups, combos) }

    emit:
    samplesheet     = ch_samplesheet     // channel: one validated meta per plate
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
// Check the structure of a parsed analysis groups file (Tier 1 validation: anything knowable
// from the file alone, without reading platemaps). See docs/analysis_groups_format.md.
// Returns [errors: [...], warnings: [...]] so that every problem can be reported at once.
//
def validateGroupingStructure(parsed) {
    def errors = []
    def warnings = []
    def grouping_keys = ['name', 'grouping_column', 'groups', 'always_include', 'excluded', 'filter', 'subgroup_columns']

    if (!(parsed instanceof Map)) {
        errors << "The analysis groups file must be a mapping with 'default', 'cell', and/or 'well' keys."
        return [errors: errors, warnings: warnings]
    }
    def unknown_levels = parsed.keySet().collect { key -> key as String } - ['default', 'cell', 'well']
    unknown_levels.each { key ->
        errors << "Unknown top-level key '${key}': expected 'default', 'cell', and/or 'well'."
    }

    def level_groupings = ['default', 'cell', 'well'].collectEntries { level ->
        def groupings = parsed.get(level)
        if (groupings != null && !(groupings instanceof List)) {
            errors << "Top-level key '${level}' must be a list of groupings."
            groupings = []
        }
        [(level): groupings ?: []]
    }

    level_groupings.each { level, groupings ->
        groupings.eachWithIndex { grouping, idx ->
            def label = "Grouping ${idx + 1} in '${level}'"
            if (!(grouping instanceof Map)) {
                errors << "${label} ('${grouping}') must be a mapping with 'name', 'grouping_column', and 'groups' keys."
                return
            }
            if (grouping.name != null) {
                label = "Grouping '${grouping.name}' in '${level}'"
            }

            // keys
            (grouping.keySet().collect { key -> key as String } - grouping_keys).each { key ->
                errors << "${label}: unknown key '${key}'."
            }
            ['name', 'grouping_column', 'groups'].findAll { key -> grouping.get(key) == null }.each { key ->
                errors << "${label}: missing required key '${key}'."
            }

            // name
            if (grouping.name != null && !((grouping.name as String) ==~ /[A-Za-z0-9_-]+/)) {
                errors << "${label}: name may contain only letters, digits, '_', and '-'."
            }

            // grouping_column
            def grouping_column = null
            if (grouping.grouping_column != null) {
                if (!(grouping.grouping_column instanceof String) || (grouping.grouping_column as String).contains(',')) {
                    errors << "${label}: grouping_column must be a single column name; use subgroup_columns to subdivide by other columns."
                }
                else {
                    grouping_column = addMetadataPrefix(grouping.grouping_column as String)
                }
            }

            // groups
            def groups = grouping.groups
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
                if (grouping.get(key) != null && !(grouping.get(key) instanceof List)) {
                    errors << "${label}: ${key} must be a list."
                }
            }
            if (grouping.filter != null) {
                if (!(grouping.filter instanceof Map)) {
                    errors << "${label}: filter must be a mapping of column names to lists of values."
                }
                else {
                    grouping.filter.each { col, values ->
                        if (!(values instanceof List)) {
                            errors << "${label}: filter column '${col}' must be a list of values."
                        }
                    }
                }
            }

            // cross-key checks
            if (grouping_column) {
                if (grouping.filter instanceof Map && grouping.filter.keySet().any { col -> addMetadataPrefix(col as String) == grouping_column }) {
                    errors << "${label}: filter must not name the grouping_column; use excluded to drop its values."
                }
                if (grouping.subgroup_columns instanceof List && grouping.subgroup_columns.any { col -> addMetadataPrefix(col as String) == grouping_column }) {
                    errors << "${label}: subgroup_columns must not name the grouping_column."
                }
            }
            if (grouping.always_include instanceof List && grouping.excluded instanceof List) {
                def overlap = grouping.always_include.collect { value -> value as String }
                    .intersect(grouping.excluded.collect { value -> value as String })
                if (overlap) {
                    errors << "${label}: values ${overlap} are in both always_include and excluded."
                }
            }
            if (groups == 'all_values' && grouping.always_include) {
                warnings << "${label}: always_include has no effect with 'groups: all_values'."
            }
        }
    }

    // names must be unique within each level, counting the default groupings added to it
    def default_names = level_groupings['default'].findAll { grouping -> grouping instanceof Map && grouping.name != null }
        .collect { grouping -> grouping.name as String }
    ['cell', 'well'].each { level ->
        def names = default_names + level_groupings[level]
            .findAll { grouping -> grouping instanceof Map && grouping.name != null }
            .collect { grouping -> grouping.name as String }
        names.countBy { name -> name }.findAll { _name, count -> count > 1 }.each { name, _count ->
            errors << "Grouping name '${name}' is used more than once in '${level}' (including 'default' groupings)."
        }
    }

    return [errors: errors, warnings: warnings]
}

//
// Normalize a structurally valid parsed analysis groups file into a map of
// aggregate_level ('cell'/'well') → list of groupings. Each grouping has every key filled in:
//   [name, grouping_column, groups, always_include, excluded, filter, subgroup_columns]
// Column names get the Metadata_ prefix, and all values are coerced to strings to match
// the values read from platemaps. 'default' groupings are placed first in each level.
//
def normalizeGroupings(parsed) {
    def default_groupings = (parsed.get('default') ?: []).collect { grouping -> normalizeGrouping(grouping) }
    return ['cell', 'well'].collectEntries { level ->
        [(level): default_groupings + (parsed.get(level) ?: []).collect { grouping -> normalizeGrouping(grouping) }]
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
// If no file is given, each level gets a single grouping named 'all' with no grouping column,
// meaning "one all-encompassing group per level" (no partitioning).
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
// Columns PARTITION_CONDITIONS must split by: the union of every grouping's grouping_column,
// subgroup_columns, and filter columns across both levels.
//
def groupingPartitionColumns(parsed_groups) {
    def groupings = parsed_groups.cell + parsed_groups.well
    def columns = groupings.collectMany { grouping ->
        [grouping.grouping_column] + grouping.subgroup_columns + grouping.filter.keySet()
    }
    return columns.findAll().unique() as List<String>
}

//
// Combine the normalized analysis groups with the condition combos of every plate (from
// enumeratePlateCombos) to produce the lookup createGroupChannels uses to route partitions:
//
//   [
//     partition_columns: ['Metadata_treatment', ...],
//     cell: [ (partition_key): [[group_meta, expected_size], ...], ... ],
//     well: [ ... ],
//   ]
//
// A partition_key is a combo's partition column values plus project, which is what each
// partition's meta carries after expandHivePartitions. Every observed partition key is
// present at both levels; an empty list means the partition belongs to no group at that
// level (e.g., it was filtered out or excluded).
//
// group_meta identifies the group:
//   [project, aggregate_level, grouping_name, grouping_column, group, subgroup_values, group_id, group_tag]
// group_tag (see groupTag) names the group's output files.
// Expected sizes count the (plate × combo) items in each group. They're used as the `size`
// argument to groupKey(...) so groupTuple can emit each group eagerly rather than waiting for
// the upstream channel to close.
//
// Sizes are upper bounds: COSMICQC_FILTER may drop wells, in which case the corresponding
// partition won't materialise and that group falls back to channel-close emission via
// remainder: true.
//
def buildAnalysisGroups(parsed_groups, combos) {
    def partition_columns = groupingPartitionColumns(parsed_groups)
    def partition_keys = combos.collect { combo -> combo.subMap(partition_columns + ['project']) }.unique()

    def level_lookups = ['cell', 'well'].collectEntries { level ->
        def lookup = partition_keys.collectEntries { partition_key -> [(partition_key): []] }
        parsed_groups[level].each { grouping ->
            assignGroupingSets(grouping, combos).each { group_key, members ->
                def group_meta = [
                    project: group_key.project,
                    aggregate_level: level,
                    grouping_name: grouping.name,
                    grouping_column: grouping.grouping_column,
                    group: group_key.group,
                    subgroup_values: group_key.subgroup_values,
                    group_id: groupId(grouping.grouping_column, group_key.group, group_key.subgroup_values),
                ]
                group_meta = group_meta + [group_tag: groupTag(group_meta)]
                def group_with_size = [group_meta, members.size()]
                members.each { combo ->
                    def partition_groups = lookup[combo.subMap(partition_columns + ['project'])]
                    // the same partition key can come from several plates; list each group once
                    if (!partition_groups.contains(group_with_size)) {
                        partition_groups << group_with_size
                    }
                }
            }
        }
        [(level): lookup]
    }
    return [partition_columns: partition_columns] + level_lookups
}

//
// Assign condition combos to the grouping sets (groups) of a single normalized grouping. This is the single
// definition of group membership (see docs/analysis_groups_format.md):
//   1. keep only combos matching every `filter` column
//   2. drop combos whose grouping_column value is in `excluded`
//   3. assign the rest to groups by value: the value itself (one_per_value), 'all_values',
//      or every named group listing the value (named groups may overlap)
//   4. add `always_include` combos to every group with the same project and subgroup values;
//      they don't form groups of their own (except with all_values, where they are ordinary)
// Returns a map of group key [project, group, subgroup_values] → list of member combos.
//
def assignGroupingSets(grouping, combos) {
    def filtered = combos.findAll { combo -> matchesGroupingFilter(grouping, combo) }
        .findAll { combo -> !(combo[grouping.grouping_column] in grouping.excluded) }
    def always_included = grouping.groups == 'all_values' ? [] : grouping.always_include
    def shared = filtered.findAll { combo -> combo[grouping.grouping_column] in always_included }
    def core = filtered.findAll { combo -> !(combo[grouping.grouping_column] in always_included) }

    def groups = [:]
    core.each { combo ->
        def value = combo[grouping.grouping_column]
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
            def group_key = [project: combo.project, group: group_name, subgroup_values: combo.subMap(grouping.subgroup_columns)]
            groups.get(group_key, []) << combo
        }
    }
    shared.each { combo ->
        def subgroup_values = combo.subMap(grouping.subgroup_columns)
        groups.findAll { group_key, _members -> group_key.project == combo.project && group_key.subgroup_values == subgroup_values }
            .each { _group_key, members -> members << combo }
    }
    return groups
}

//
// Whether a combo passes a grouping's filter: its value must be one of the listed values
// for every filter column.
//
def matchesGroupingFilter(grouping, combo) {
    return grouping.filter.every { col, values -> combo[col] in values }
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
// Check the analysis groups against the condition combos observed in the platemaps
// (Tier 2 validation, see docs/analysis_groups_format.md). Returns a list of error messages:
//   - a filter column missing from any plate's platemap
//   - a filter that matches no rows
//   - named groups that don't account for an observed (filtered) value
//   - always_include values absent from the filtered rows
//   - always_include values absent from any subgroup (within a project) that has other rows
// Groupings shared by both levels (default groupings) are checked once.
//
def validateGroupingsAgainstData(parsed_groups, combos) {
    def errors = []
    def groupings = (parsed_groups.cell + parsed_groups.well).unique()
    groupings.findAll { grouping -> grouping.grouping_column }.each { grouping ->
        def label = "Grouping '${grouping.name}'"

        // filter columns must be present in every platemap, or those plates silently drop out
        grouping.filter.keySet().each { col ->
            def plates = combos.findAll { combo -> combo[col] == '_Absent_' }.collect { combo -> combo.plate_id }.unique()
            if (plates) {
                errors << "${label}: filter column '${col}' is missing from the platemap for plate(s) ${plates.join(', ')}."
            }
        }

        def filtered = combos.findAll { combo -> matchesGroupingFilter(grouping, combo) }
        if (grouping.filter && !filtered) {
            errors << "${label}: filter ${grouping.filter} matches no rows."
            return
        }
        def observed = filtered.collect { combo -> combo[grouping.grouping_column] }.unique()

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
        def core = filtered.findAll { combo ->
            !(combo[grouping.grouping_column] in grouping.always_include) && !(combo[grouping.grouping_column] in grouping.excluded)
        }
        core.collect { combo -> [project: combo.project] + combo.subMap(grouping.subgroup_columns) }.unique().each { subgroup ->
            def subgroup_values = filtered.findAll { combo -> combo.subMap(subgroup.keySet()) == subgroup }
                .collect { combo -> combo[grouping.grouping_column] }
            def subgroup_missing = present.findAll { value -> !(value in subgroup_values) }
            if (subgroup_missing) {
                errors << "${label}: always_include values ${subgroup_missing} were not found in subgroup ${subgroup}."
            }
        }
    }
    return errors
}

//
// List the condition combos for one plate: each unique combination of partition column values
// in its platemap, plus project and plate_id. This defines every partition a plate can produce.
// Plate-level metadata from the samplesheet (not yet supported) would be merged in here.
//
def enumeratePlateCombos(meta, partition_columns) {
    return enumeratePlatemapCombos(meta.platemap_file, partition_columns)
        .collect { combo -> combo + [project: meta.project, plate_id: meta.plate_id] }
}

//
// Read a platemap file and return the list of unique condition combinations present on the plate.
// Each combination is a Map keyed by Metadata_-prefixed column names → row values, read the same
// way pycytominer_annotate.py reads the platemap before partitioning: values are literal text with
// surrounding whitespace stripped, and
//   - blank cell                                 → '_NA_'
//   - condition column absent from this platemap → '_Absent_' (mirrors the plate_metadata_columns fill)
// Keeping the encoding aligned is what lets buildAnalysisGroups predict partition keys that
// actually match the values recorded by PARTITION_CONDITIONS.
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
