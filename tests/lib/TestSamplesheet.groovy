//
// Samplesheet helper for nf-test pipeline tests (tests/lib is on the nf-test classpath)
//
import com.opencsv.CSVReader
import com.opencsv.CSVWriter

class TestSamplesheet {

    // samplesheet columns that hold file or directory paths
    static final List<String> PATH_COLUMNS = ['sqlite_path', 'parquet_path', 'platemaps_dir', 'platemap_file']

    //
    // Write a copy of a samplesheet to out_dir with its relative path columns made absolute, and return the copy's path.
    // Samplesheet paths are relative to the repo root (the usual launch directory),
    // but nf-test runs pipeline tests in an isolated launch directory, where they would not otherwise resolve.
    // Absolute paths and URIs (e.g. s3://) are left unchanged.
    // Uses the opencsv library bundled with nf-test, so quoted fields are read and written correctly.
    //
    static String withAbsolutePaths(String samplesheet, String project_dir, String out_dir) {
        def rows = new File(samplesheet).withReader { reader -> new CSVReader(reader).readAll() }
        def path_idx = rows[0].findIndexValues { col -> col in PATH_COLUMNS }.collect { i -> i as int }
        rows.drop(1).each { vals ->
            path_idx.each { i ->
                def val = vals[i]
                if (val && !new File(val).isAbsolute() && !(val =~ /^[A-Za-z][\w+.-]*:\/\//)) {
                    vals[i] = new File(project_dir, val).path
                }
            }
        }
        def out_file = new File(out_dir, 'samplesheet.csv')
        out_file.parentFile.mkdirs()
        // quote fields only where needed
        out_file.withWriter { writer -> new CSVWriter(writer).writeAll(rows, false) }
        return out_file.path
    }
}
