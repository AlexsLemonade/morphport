#!/usr/bin/env python3
"""
Nextflow template script to apply coSMicQC outlier detection to a per-plate parquet file.
Filter conditions and z-score thresholds are loaded from a JSON settings file.
Cells failing any condition are removed; outputs include a filtered parquet,
a compressed CSV of outlier indices, and a JSON summary of settings and results.
"""

import json
import math
import re
import sys
from importlib.metadata import version

import cosmicqc
import pandas as pd

# Standard cell-identifier columns; plate/well/site identify the image,
# ImageNumber + ObjectNumber uniquely identify the cell within it.
REQUIRED_METADATA = [
    "Metadata_Plate",
    "Metadata_Well",
    "Metadata_ImageNumber",
    "Metadata_ObjectNumber",
]

OPTIONAL_METADATA = [
    "Image_Metadata_Site",
    "Cells_Number_Object_Number",
    "Nuclei_Number_Object_Number",
    "Cytoplasm_Number_Object_Number",
]

# Standard microplate formats: (n_rows, n_cols) for each supported well count.
PLATE_FORMATS = {
    96: (8, 12),
    384: (16, 24),
    1536: (32, 48),
}


def is_valid_threshold(value) -> bool:
    """
    Whether a value is usable as a z-score threshold: a finite, non-zero number.
    Booleans are excluded even though Python treats them as integers, and zero is excluded
    because coSMicQC treats it as negative, flagging every cell below the mean.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    return math.isfinite(value) and value != 0


def validate_filter_conditions(conditions) -> list[str]:
    """
    Check the structure of the outlier conditions loaded from the settings file.
    Returns a list of error messages, so that every problem can be reported at once.
    """
    if not isinstance(conditions, list):
        return ["The settings file must be a JSON array of outlier condition objects."]
    errors = []
    for position, condition in enumerate(conditions, start=1):
        label = f"Outlier condition {position}"
        if not isinstance(condition, dict) or {"name", "thresholds"} - condition.keys():
            errors.append(f"{label}: must have 'name' and 'thresholds' keys.")
            continue
        if not isinstance(condition["name"], str) or not condition["name"]:
            errors.append(f"{label}: 'name' must be a non-empty string.")
        else:
            label = f"{label} ('{condition['name']}')"
        if not isinstance(condition["thresholds"], dict) or not condition["thresholds"]:
            errors.append(
                f"{label}: 'thresholds' must be a non-empty mapping of feature names to z-scores."
            )
            continue
        for feature, value in condition["thresholds"].items():
            if not is_valid_threshold(value):
                errors.append(
                    f"{label}: threshold for '{feature}' must be a non-zero number, got {json.dumps(value)}."
                )
    # outlier counts and skips are reported by name, so names must be unique
    names = [
        condition["name"]
        for condition in conditions
        if isinstance(condition, dict) and isinstance(condition.get("name"), str)
    ]
    duplicates = sorted({name for name in names if names.count(name) > 1})
    for name in duplicates:
        errors.append(f"Outlier condition name '{name}' is used more than once.")
    return errors


def write_versions(task_process: str):
    with open("versions.yml", "w") as f:
        f.write(f'"{task_process}":\\n')
        f.write(f"    python: {sys.version.split()[0]}\\n")
        f.write(f"    cosmicqc: {version('cosmicqc')}\\n")
        f.write(f"    pandas: {version('pandas')}\\n")


def row_letters_to_index(row_letters: str) -> int:
    """Convert a alphabetical row label ('A', ..., 'H', ..., 'AB', ...) to a 1-based index."""
    index = 0
    for char in row_letters.upper():
        index = index * 26 + (ord(char) - ord("A") + 1)
    return index


def infer_plate_format(max_row: int, max_col: int) -> int:
    """Infer the plate size (96, 384, or 1536) from the highest observed row/column indices."""
    for plate_size, (n_rows, n_cols) in sorted(PLATE_FORMATS.items()):
        if max_row <= n_rows and max_col <= n_cols:
            return plate_size
    raise ValueError(
        f"Wells span row {max_row} / column {max_col}, which doesn't fit a standard plate format."
    )


def find_edge_wells(wells: list[str]) -> tuple[int, set[str]]:
    """
    Given an iterable of well ids like 'A01', 'H12', 'AB28', infer the plate size (96, 384, or
    1536, based on the highest row/column indices among the wells present) and return it along
    with the subset of wells that fall in that plate's outermost rows or columns.

    This function assumes that all wells in a plate are filled with media; if some
    wells are empty, the inferred edge wells may be inaccurate.
    """
    well_id_pattern = re.compile(r"([A-Za-z]+)([0-9]+)")
    parsed = {}
    for well in wells:
        match = well_id_pattern.fullmatch(well)
        if not match:
            raise ValueError(f"Could not parse well id '{well}' into row/column")
        row_letters, col_digits = match.groups()
        parsed[well] = (row_letters_to_index(row_letters), int(col_digits))

    max_row = max(row for row, _ in parsed.values())
    max_col = max(col for _, col in parsed.values())
    plate_size = infer_plate_format(max_row, max_col)
    n_rows, n_cols = PLATE_FORMATS[plate_size]

    edge_wells = {
        well
        for well, (row, col) in parsed.items()
        if row in (1, n_rows) or col in (1, n_cols)
    }
    return plate_size, edge_wells


def plural(count: int, singular: str, plural_form: str | None = None) -> str:
    """Return the singular word for a count of 1, and the plural otherwise."""
    if count == 1:
        return singular
    return plural_form if plural_form else f"{singular}s"


def find_skip_reason(df: pd.DataFrame, features: list[str]) -> dict | None:
    """
    Return why an outlier condition with these features can't be scored on the cells in df,
    as {"reason": ..., "features": [...]}, or None if it can be scored.
    Reasons, checked in order:
    - no_cells: df has no cells
    - missing_features: some features are not columns of df
    - all_nan: some features have missing values in every cell
    """
    if len(df) == 0:
        return {"reason": "no_cells", "features": []}
    missing = [feature for feature in features if feature not in df.columns]
    if missing:
        return {"reason": "missing_features", "features": missing}
    all_nan = [feature for feature in features if df[feature].isna().all()]
    if all_nan:
        return {"reason": "all_nan", "features": all_nan}
    return None


def format_skip_warning(name: str, skip: dict) -> str:
    """Format a warning message for a skipped outlier condition."""
    features = ", ".join(skip["features"])
    if skip["reason"] == "missing_features":
        detail = f"features not found in the profiles: {features}"
    else:
        detail = f"features with missing values in every cell: {features}"
    return f"Outlier condition '{name}' was skipped; {detail}."


def count_missing_values(
    df: pd.DataFrame, features: list[str]
) -> tuple[int, list[str]]:
    """
    Return the number of cells with a missing value in any of the features,
    and the features that have missing values.
    """
    missing = df[features].isna()
    nan_features = [feature for feature in features if missing[feature].any()]
    return int(missing.any(axis=1).sum()), nan_features


def main():
    # capture input variables from Nextflow
    parquet_file = "${parquet_file}"
    filter_settings_file = "${cosmicqc_filter_file}"
    plate_id = "${meta.plate_id}"
    task_process = "${task.process}"
    edge_wells_included = "${meta.edge_wells_included}" == "true"

    with open(filter_settings_file) as f:
        filter_conditions = json.load(f)
    settings_errors = validate_filter_conditions(filter_conditions)
    if settings_errors:
        print(
            f"Invalid outlier condition settings in {filter_settings_file}:",
            file=sys.stderr,
        )
        for message in settings_errors:
            print(f"- {message}", file=sys.stderr)
        sys.exit(1)

    df = pd.read_parquet(parquet_file)
    # cosmicqc returns outliers as a CytoDataFrame, which scans text columns for image file names;
    # that scan fails on pandas string dtypes (e.g. string[pyarrow] columns from annotation)
    # when a condition finds no outliers, so use plain object strings
    df = df.astype({col: object for col in df.select_dtypes(include="string").columns})

    # check for required metadata columns and include any optional ones that are present
    missing_metadata = set(REQUIRED_METADATA) - set(df.columns)
    if missing_metadata:
        raise ValueError(
            f"Input parquet file is missing required metadata columns: {missing_metadata}"
        )
    metadata_cols = REQUIRED_METADATA + [
        col for col in OPTIONAL_METADATA if col in df.columns
    ]

    # optionally drop wells on the border of the plate before coSMicQC outlier detection.
    # edge wells are identified based on a standard 96-, 384-, or 1536-well plate layout,
    # inferred from the wells present.
    # an empty plate has no wells to infer the plate format from, so edge filtering is skipped
    cells_before_edge_filter = len(df)
    plate_size = None
    removed_edge_wells = []
    if not edge_wells_included and cells_before_edge_filter > 0:
        plate_size, edge_wells = find_edge_wells(df["Metadata_Well"].unique())
        removed_edge_wells = sorted(edge_wells)
        df = df[~df["Metadata_Well"].isin(removed_edge_wells)]

    # cell_count reflects the population after edge-well removal (if any), since that's the
    # population coSMicQC's outlier detection uses
    cell_count = len(df)

    warning_messages = []
    if cells_before_edge_filter == 0:
        warning_messages.append(
            "The plate has no cells, so no outlier conditions were applied."
        )
    elif cell_count == 0:
        warning_messages.append(
            "No cells remain after removing edge wells, so no outlier conditions were applied."
        )

    # apply the filter conditions and collect outliers for summary output
    outlier_dataframes = []
    condition_outlier_counts = {}
    skipped_conditions = {}
    for condition in filter_conditions:
        name = condition["name"]
        features = list(condition["thresholds"])
        # coSMicQC fails with no cells, so conditions that can't be scored are skipped
        skip = find_skip_reason(df, features)
        if skip:
            skipped_conditions[name] = skip
            if skip["reason"] != "no_cells":
                warning_messages.append(format_skip_warning(name, skip))
            continue
        # coSMicQC drops cells with missing values before scoring, so they are never flagged
        nan_cells, nan_features = count_missing_values(df, features)
        if nan_cells > 0:
            warning_messages.append(
                f"Outlier condition '{name}': {nan_cells} {plural(nan_cells, 'cell')} with missing values"
                f" in {', '.join(nan_features)} {plural(nan_cells, 'was', 'were')} not evaluated and kept."
            )
        outliers = cosmicqc.find_outliers(
            df=df,
            metadata_columns=metadata_cols,
            feature_thresholds=condition["thresholds"],
        )
        outlier_dataframes.append((name, outliers))
        condition_outlier_counts[name] = len(outliers)

    if cell_count > 0 and filter_conditions and not condition_outlier_counts:
        warning_messages.append(
            "Every outlier condition was skipped, so no outlier cells were removed."
        )

    # add a column with the condition name to each outlier dataframe
    # drop empty dataframes to avoid concatenation errors
    labeled_outliers = [
        outliers.assign(cosmicqc_condition=name)
        for name, outliers in outlier_dataframes
        if len(outliers) > 0
    ]
    if labeled_outliers:
        # concatenate all outliers and aggregate by cell index to combine conditions
        combined = pd.concat(labeled_outliers)
        # build a pandas aggregation dict that takes
        # - the first value for metadata columns (will all be the same for a given cell)
        # - concatenated condition list for the failure reasons (in case a cell fails multiple conditions)
        agg_dict = {col: "first" for col in metadata_cols}
        agg_dict["cosmicqc_condition"] = lambda x: ";".join(sorted(x.unique()))
        ## aggregate to get one row per outlier cell, with all conditions that flagged it combined in the cosmicqc_condition column
        outlier_summary = combined.groupby(combined.index).agg(agg_dict)
        outlier_indices = outlier_summary.index
    else:
        # if no outliers, create an empty dataframe with the correct columns for consistency
        outlier_summary = pd.DataFrame(columns=metadata_cols + ["cosmicqc_condition"])
        outlier_indices = pd.Index([])

    # create the filtered dataframe by dropping outlier rows from the original dataframe
    df_filtered = df.drop(outlier_indices)
    # "NA" when there were no cells to compute a fraction from (bare NaN isn't valid JSON and
    # can't be parsed downstream by Nextflow's JsonSlurper, so we use a quoted string instead)
    fraction_removed = len(outlier_indices) / cell_count if cell_count > 0 else "NA"
    if cell_count > 0 and len(df_filtered) == 0:
        warning_messages.append(
            "Every cell was removed as an outlier, so no cells remain for this plate."
        )

    # create a QC summary object
    qc_summary = {
        "plate_id": plate_id,
        "filter_settings": filter_conditions,
        "cosmicqc_filtering": {
            "cells_input": cell_count,
            "cells_output": len(df_filtered),
            "fraction_removed": fraction_removed,
            "condition_outlier_counts": condition_outlier_counts,
            "skipped_conditions": skipped_conditions,
        },
        "edge_filtering": {
            "edge_wells_included": edge_wells_included,
            "plate_size": plate_size,
            "wells_removed": len(removed_edge_wells),
            "edge_wells": removed_edge_wells,
            "cells_input": cells_before_edge_filter,
            "cells_output": cell_count,
        },
    }

    # save outputs
    df_filtered.to_parquet(f"filtered_{plate_id}.parquet", index=False)
    # include the index for outlier summary since we should be able to use it to link back
    # to the original dataframe when building a QC report
    outlier_summary.to_csv(f"cosmicqc_outliers_{plate_id}.csv.gz", index=True)
    with open(f"cosmicqc_summary_{plate_id}.json", "w") as f:
        json.dump(qc_summary, f, indent=2)
    # the warnings file is only written when there are warnings
    if warning_messages:
        with open(f"{plate_id}_cosmicqc_warnings.txt", "w") as f:
            for message in warning_messages:
                print(f"WARNING: {message}", file=sys.stderr)
                f.write(f"{message}\\n")

    write_versions(task_process)


if __name__ == "__main__":
    main()
