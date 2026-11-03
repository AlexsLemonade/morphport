# Tests

Pipeline tests live in this directory (e.g. `default.nf.test`).
Module tests live next to each module, in `modules/local/<module>/tests/main.nf.test`.
See `CLAUDE.md` and `docs/CONTRIBUTING.md` for how to run them.

## Helpers in `tests/lib`

Groovy classes in `tests/lib` are on the nf-test classpath, so they can be used directly in any test.
Changes to them trigger a full test run (see `triggers` in `nf-test.config`).

- `TestSamplesheet`: makes the relative paths in a samplesheet absolute for pipeline tests.
- `ParquetTools`: parquet utilities that complement the nft-parquet plugin (see below).

## Checking parquet outputs

Read parquet files with `ParquetTools.read(file)` from `tests/lib`, which accepts a path string, `File`, or nf-test `path(...)`.
Its properties follow the names of the [nft-parquet](https://github.com/incsteps/nft-parquet) plugin's `path(file).parquet`, so they can be used with `with`:

- `rowCount`: the number of rows.
- `columnCount`: the number of columns.
- `columnNames`: the column names, in file order.
- `schema`: an ordered map of column name to the Java class of its values (e.g. `[leaf_id: Long, distance: Double, label: String]`).
- `rows`: all rows, as a list of value lists in file column and row order (reads the whole file, so only use it for small outputs).
- `rowIndex(n)`: a single row (0-based), as a list of values.

Counts, column names, and the schema are read from the file footer, so they also work for files with zero rows (e.g. outputs of skipped groups).

Test each property of a parquet output individually.
Don't snapshot parquet md5s (they change with the writing library's version) or values calculated by the pipeline.
Floating point values are not rounded, so compare them with a tolerance rather than exactly.

Example:

```groovy
then {
    assert process.success
    with(ParquetTools.read(process.out.result[0][1])) {
        assert rowCount == 5
        assert columnNames == ['left', 'right', 'distance', 'n_members']
        assert schema == [left: Long, right: Long, distance: Double, n_members: Long]
    }
    with(ParquetTools.read(process.out.result[0][2])) {
        // leaf ids are 0..n-1
        assert rows.collect { row -> row[0] } == (0..<6).toList()
    }
}
```

`ParquetTools` uses the parquet-java and carpet-record libraries bundled with the nft-parquet plugin, which `nf-test.config` loads only for that reason.
The plugin's own API (as of 0.0.1) only provides `rowCount`, so use `ParquetTools` rather than `path(file).parquet`.
