#!/usr/bin/env python3
"""
Applies coSMicQC outlier detection to a per-plate parquet file.
Filter conditions and z-score thresholds are loaded from a JSON settings file.
Cells failing any condition are removed; outputs include a filtered parquet,
a compressed CSV of outlier indices, and a JSON summary of settings and results.
"""

import json
import sys

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


def write_versions(task_process):
    """Write versions.yml file for Nextflow tracking"""
    with open("versions.yml", "w") as f:
        f.write(
            "\\n".join(
                [
                    f'"{task_process}":',
                    f"    python: {sys.version.split()[0]}",
                    f"    cosmicqc: {cosmicqc.__version__}",
                    f"    pandas: {pd.__version__}",
                ]
            )
            + "\\n"
        )


def main():
    # capture input variables from Nextflow
    parquet_file = "${parquet_file}"
    filter_settings_file = "${cosmicqc_filter_file}"
    plate_id = "${meta.plate_id}"
    task_process = "${task.process}"

    with open(filter_settings_file) as f:
        filter_conditions = json.load(f)
    validate_filter_conditions(filter_conditions, filter_settings_file)

    df = pd.read_parquet(parquet_file)
    cells_input = len(df)

    # check for required metadata columns and include any optional ones that are present
    missing_metadata = set(REQUIRED_METADATA) - set(df.columns)
    if missing_metadata:
        raise ValueError(
            f"Input parquet file is missing required metadata columns: {missing_metadata}"
        )
    metadata_cols = REQUIRED_METADATA + [
        col for col in OPTIONAL_METADATA if col in df.columns
    ]

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
    fraction_removed = len(outlier_indices) / cells_input if cells_input > 0 else 0.0

    # create a QC summary object
    qc_summary = {
        "plate_id": plate_id,
        "filter_settings": filter_conditions,
        "results": {
            "cells_input": cells_input,
            "cells_output": len(df_filtered),
            "fraction_removed": fraction_removed,
            "condition_outlier_counts": condition_outlier_counts,
            "skipped_conditions": skipped_conditions,
        },
    }

    # save outputs
    df_filtered.to_parquet(f"{plate_id}_filtered.parquet", index=False)
    # include the index for outlier summary since we should be able to use it to link back
    # to the original dataframe when building a QC report
    outlier_summary.to_csv(f"{plate_id}_cosmicqc_outliers.csv.gz", index=True)
    with open(f"{plate_id}_cosmicqc_summary.json", "w") as f:
        json.dump(qc_summary, f, indent=2)

    write_versions(task_process)


if __name__ == "__main__":
    main()
