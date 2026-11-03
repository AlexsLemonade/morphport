//
// Parquet reader for nf-test assertions (tests/lib is on the nf-test classpath)
//
// Usage, with the same property names as the nft-parquet plugin's `path(file).parquet`:
//
//     with(ParquetTools.read(file)) {
//         assert rowCount == 5
//         assert columnNames == ['left', 'right', 'distance', 'n_members']
//         assert schema == [left: Long, right: Long, distance: Double, n_members: Long]
//         assert rows[0][0] == 3
//     }
//
// The nft-parquet plugin (loaded in nf-test.config) is used only for the parquet-java and carpet-record
// libraries it bundles: as of 0.0.1 its own API only provides `rowCount`.
// Row and column counts, column names, and the schema are read from the file footer,
// so they also work for files with zero rows (e.g. outputs of skipped groups).
//
import java.nio.file.Path
import java.nio.file.Paths

import com.jerolba.carpet.CarpetReader
import org.apache.parquet.hadoop.ParquetFileReader
import org.apache.parquet.io.LocalInputFile
import org.apache.parquet.schema.LogicalTypeAnnotation
import org.apache.parquet.schema.PrimitiveType.PrimitiveTypeName
import org.apache.parquet.schema.Type

class ParquetTools {

    private final Path file
    private final long row_count
    private final List<Type> fields

    //
    // Open a parquet file (a path string, File, or nf-test path) and read its footer.
    //
    static ParquetTools read(Object parquet_path) {
        return new ParquetTools(Paths.get(parquet_path.toString()))
    }

    private ParquetTools(Path file) {
        this.file = file
        def reader = ParquetFileReader.open(new LocalInputFile(file))
        try {
            this.row_count = reader.recordCount
            this.fields = reader.footer.fileMetaData.schema.fields
        }
        finally {
            reader.close()
        }
    }

    // The number of rows.
    long getRowCount() {
        return row_count
    }

    // The number of columns.
    int getColumnCount() {
        return fields.size()
    }

    // The column names, in file column order.
    List<String> getColumnNames() {
        return fields.collect { field -> field.name }
    }

    //
    // An ordered map of column name to the Java class of its values, in file column order,
    // e.g. [leaf_id: Long, distance: Double, label: String].
    // The classes match the values returned by `rows`.
    //
    Map<String, Class> getSchema() {
        return fields.collectEntries { field -> [field.name, javaClass(field)] }
    }

    //
    // All rows, as a list of value lists in file column and row order.
    // Reads the whole file into memory, so only use this for small files.
    //
    List<List> getRows() {
        def columns = columnNames
        return new CarpetReader(file.toFile(), Map).toList().collect { record -> columns.collect { col -> record[col] } }
    }

    //
    // A single row (0-based), as a list of values in file column order.
    //
    List rowIndex(long index) {
        if (index < 0 || index >= row_count) {
            throw new IndexOutOfBoundsException("Row index ${index} out of range for ${file} with ${row_count} rows")
        }
        def columns = columnNames
        def record = new CarpetReader(file.toFile(), Map).stream().skip(index).findFirst().get()
        return columns.collect { col -> record[col] }
    }

    String toString() {
        return "ParquetTools(${file}: ${row_count} rows, ${fields.size()} columns)"
    }

    private static Class javaClass(Type field) {
        def logical_type = field.logicalTypeAnnotation
        if (!field.isPrimitive()) {
            if (logical_type instanceof LogicalTypeAnnotation.ListLogicalTypeAnnotation) {
                return List
            }
            // maps and structs
            return Map
        }
        if (logical_type instanceof LogicalTypeAnnotation.StringLogicalTypeAnnotation
            || logical_type instanceof LogicalTypeAnnotation.EnumLogicalTypeAnnotation
            || logical_type instanceof LogicalTypeAnnotation.JsonLogicalTypeAnnotation) {
            return String
        }
        if (logical_type instanceof LogicalTypeAnnotation.DateLogicalTypeAnnotation) {
            return java.time.LocalDate
        }
        if (logical_type instanceof LogicalTypeAnnotation.TimestampLogicalTypeAnnotation) {
            return logical_type.isAdjustedToUTC() ? java.time.Instant : java.time.LocalDateTime
        }
        if (logical_type instanceof LogicalTypeAnnotation.DecimalLogicalTypeAnnotation) {
            return BigDecimal
        }
        switch (field.asPrimitiveType().primitiveTypeName) {
            case PrimitiveTypeName.BOOLEAN:
                return Boolean
            case PrimitiveTypeName.INT32:
                // narrower integer types (e.g. polars Int8/Int16) are stored as INT32 with an integer annotation
                if (logical_type instanceof LogicalTypeAnnotation.IntLogicalTypeAnnotation && logical_type.bitWidth < 32) {
                    return logical_type.bitWidth == 8 ? Byte : Short
                }
                return Integer
            case PrimitiveTypeName.INT64:
                return Long
            case PrimitiveTypeName.FLOAT:
                return Float
            case PrimitiveTypeName.DOUBLE:
                return Double
            default:
                // BINARY, FIXED_LEN_BYTE_ARRAY, INT96 without a recognized logical type
                return byte[]
        }
    }
}
