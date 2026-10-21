//
// Samplesheet helper for nf-test pipeline tests (tests/lib is on the nf-test classpath)
//
class TestSamplesheet {

    // samplesheet columns that hold file or directory paths
    static final List<String> PATH_COLUMNS = ['sqlite_path', 'parquet_path', 'platemaps_dir', 'platemap_file']

    //
    // Write a copy of a samplesheet to out_dir with its path columns made absolute, and return the copy's path.
    // Samplesheet paths are relative to the repo root (the usual launch directory),
    // but nf-test runs pipeline tests in an isolated launch directory, where they would not otherwise resolve.
    //
    static String withAbsolutePaths(String samplesheet, String project_dir, String out_dir) {
        def rows = new File(samplesheet).readLines()
        def header = rows[0].split(',') as List
        def rewritten = [rows[0]] + rows[1..-1].collect { line ->
            def vals = line.split(',', -1)
            header.eachWithIndex { col, i ->
                if (col in PATH_COLUMNS && vals[i]) {
                    vals[i] = new File(project_dir, vals[i]).path
                }
            }
            vals.join(',')
        }
        def out_file = new File(out_dir, 'samplesheet.csv')
        out_file.parentFile.mkdirs()
        out_file.text = rewritten.join('\n') + '\n'
        return out_file.path
    }
}
