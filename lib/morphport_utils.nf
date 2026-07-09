// Shared Groovy helper functions for local modules.

// Build a filesystem-safe tag from meta:
//   <aggregate_level>__<col1>=<val1>__<col2>=<val2>
// When there are no grouping columns, falls back to "<aggregate_level>__all".
def groupTag(meta) {
    def cols = meta.grouping_columns ?: []
    def parts = cols ? cols.collect { col -> "${col.replaceFirst('^Metadata_', '')}=${meta[col]}" }.join('__') : 'all'
    "${meta.aggregate_level}__${parts}".replaceAll('[^A-Za-z0-9._=-]', '_')
}

// Bundle group identity as a single JSON blob for templates to parse, rather than
// several stringly-typed bindings. `extra` is merged in after `grouping_values`.
def groupingInfoJson(meta, extra = [:]) {
    def info = [
        grouping_values: (meta.grouping_columns ?: []).collectEntries { col -> [(col): meta[col]] },
    ] + extra
    groovy.json.JsonOutput.toJson(info)
}
