# Tests

Pipeline tests live in this directory (e.g. `default.nf.test`).
Module tests live next to each module, in `modules/local/<module>/tests/main.nf.test`.
See `CLAUDE.md` and `docs/CONTRIBUTING.md` for how to run them.

## Helpers in `tests/lib`

Groovy classes in `tests/lib` are on the nf-test classpath, so they can be used directly in any test.
Changes to them trigger a full test run (see `triggers` in `nf-test.config`).

- `TestSamplesheet`: makes the relative paths in a samplesheet absolute for pipeline tests.

## Checking parquet outputs

Read parquet files with `path(file).parquet` from the [nft-parquet](https://github.com/incsteps/nft-parquet) plugin, which provides these properties for use with `with`:

- `rowCount`: the number of rows.
- `columnCount`: the number of columns.
- `columnNames`: the column names, in file order (a `String[]`, which compares equal to a list with `==`).
- `schema`: a map of column name to the Java class of its values (e.g. `[leaf_id: Long, distance: Double, label: String]`).
- `rows`: all rows, as a list of value arrays in file column and row order (reads the whole file, so only use it for small outputs).

Counts, column names, and the schema are read from the file footer, so they also work for files with zero rows (e.g. outputs of skipped groups).

As of 0.0.4, `schema` only distinguishes the basic types: 8- and 16-bit integers are reported as `Integer`, dates and timestamps as `Integer` or `Long`, and nested columns are flattened into dotted names (e.g. `address.city`).
Avoid `rowIndex(n)` for now: in 0.0.4 `rowIndex(0)` and `rowIndex(1)` both return the first row, and `rowIndex(n)` returns row `n - 1`.

Test each property of a parquet output individually.
Don't snapshot parquet md5s (they change with the writing library's version) or values calculated by the pipeline.
Floating point values are not rounded, so compare them with a tolerance rather than exactly.

Example:

```groovy
then {
    assert process.success
    with(path(process.out.result[0][1]).parquet) {
        assert rowCount == 5
        assert columnNames == ['left', 'right', 'distance', 'n_members']
        assert schema == [left: Long, right: Long, distance: Double, n_members: Long]
    }
    with(path(process.out.result[0][2]).parquet) {
        // leaf ids are 0..n-1
        assert rows.collect { row -> row[0] } == (0..<6).toList()
    }
}
```

nft-parquet 0.0.4 is not yet in the nf-test plugin registry, so `nf-test.config` loads it from the local index in `tests/nft-plugins.json` under the id `nft-parquet-github`.
Once the registry lists 0.0.4, remove that file and the `repository` line, and load `nft-parquet@0.0.4` instead.
