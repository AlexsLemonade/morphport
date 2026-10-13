//
// Functions for parsing, validating, and building analysis groups from the analysis groups file.
// See docs/analysis_groups_format.md for the file format.
//

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
