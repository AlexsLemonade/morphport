#!/usr/bin/env python3
"""
Nextflow template script to apply coSMicQC outlier detection to a per-plate parquet file.
Filter conditions and z-score thresholds are loaded from a JSON settings file.
Cells failing any condition are removed; outputs include a filtered parquet,
a compressed CSV of outlier indices, and a JSON summary of settings and results.
"""

import json
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


def validate_filter_conditions(conditions, source):
    """Validate the structure of the cosmicqc filter conditions loaded from a JSON file"""
    if not isinstance(conditions, list):
        raise ValueError(f"{source}: must be a JSON array of condition objects")
    for i, condition in enumerate(conditions):
        prefix = f"{source} entry {i}"
        if not isinstance(condition, dict) or {"name", "thresholds"} - condition.keys():
            raise ValueError(f"{prefix}: must have 'name' and 'thresholds' keys")
        if not isinstance(condition["name"], str) or not condition["name"]:
            raise ValueError(f"{prefix}: 'name' must be a non-empty string")
        if not isinstance(condition["thresholds"], dict) or not condition["thresholds"]:
            raise ValueError(
                f"{prefix}: 'thresholds' must be a non-empty dict of feature: z-score pairs"
            )
        for feature, value in condition["thresholds"].items():
            if not isinstance(value, (int, float)):
                raise ValueError(
                    f"{prefix}: threshold for '{feature}' must be a number, got {type(value).__name__}"
                )


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


def main():
    # capture input variables from Nextflow
    parquet_file = "${parquet_file}"
    filter_settings_file = "${cosmicqc_filter_file}"
    plate_id = "${meta.plate_id}"
    task_process = "${task.process}"
    edge_wells_included = "${meta.edge_wells_included}" == "true"

    with open(filter_settings_file) as f:
        filter_conditions = json.load(f)
    validate_filter_conditions(filter_conditions, filter_settings_file)

    df = pd.read_parquet(parquet_file)

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
    cells_before_edge_filter = len(df)
    plate_size = None
    removed_edge_wells = []
    if not edge_wells_included:
        plate_size, edge_wells = find_edge_wells(df["Metadata_Well"].unique())
        removed_edge_wells = sorted(edge_wells)
        df = df[~df["Metadata_Well"].isin(removed_edge_wells)]

    # cell_count reflects the population after edge-well removal (if any), since that's the
    # population coSMicQC's outlier detection uses
    cell_count = len(df)

    # apply the filter conditions and collect outliers for summary output
    outlier_dataframes = []
    condition_outlier_counts = {}
    skipped_conditions = {}
    for condition in filter_conditions:
        missing = [f for f in condition["thresholds"] if f not in df.columns]
        if missing:
            skipped_conditions[condition["name"]] = missing
            continue
        outliers = cosmicqc.find_outliers(
            df=df,
            metadata_columns=metadata_cols,
            feature_thresholds=condition["thresholds"],
        )
        outlier_dataframes.append((condition["name"], outliers))
        condition_outlier_counts[condition["name"]] = len(outliers)

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

    write_versions(task_process)


if __name__ == "__main__":
    main()
