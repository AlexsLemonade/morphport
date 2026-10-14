// Shared Groovy helper functions for local modules.

// Build a filesystem-safe tag from a group meta (see buildAnalysisGroups):
//   <aggregate_level>__<grouping_name>__<group_id>
// e.g. cell__moa_by_cell_line__treatment=kinase_inhibitor__cell_type=lineA
// When there is no grouping column (the 'all' grouping used without an analysis groups file),
// there is only one group, so the group id is left out: "<aggregate_level>__all".
def groupTag(meta) {
    def tag = meta.grouping_column
        ? "${meta.aggregate_level}__${meta.grouping_name}__${meta.group_id}"
        : "${meta.aggregate_level}__${meta.grouping_name}"
    tag.replaceAll('[^A-Za-z0-9._=-]', '_')
}

// Bundle group identity as a single JSON blob for templates to parse, rather than
// several stringly-typed bindings. `extra` is merged in after the identity fields.
def groupingInfoJson(meta, extra = [:]) {
    def info = [
        grouping_name: meta.grouping_name,
        grouping_column: meta.grouping_column,
        group: meta.group,
        subgroup_values: meta.subgroup_values ?: [:],
    ] + extra
    groovy.json.JsonOutput.toJson(info)
}
