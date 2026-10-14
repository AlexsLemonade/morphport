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
