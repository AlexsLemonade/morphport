#!/usr/bin/env python3
"""
Nextflow template script to score the phenotypic activity of each test condition in one
analysis group with mean average precision (mAP), using copairs.

Reads the reference-normalized profiles, keeps the metadata columns and the features listed in
features_file, and then:

1. Drops features with any NaN value, since copairs needs complete profiles.
2. Drops non-reference wells with no comparison column value: a null, or the '_NA_' that
   pycytominer_annotate.py writes for a blank platemap cell.
3. Drops a test condition's wells (e.g. a treatment's) in any cell type where it has fewer
   than min_wells wells. "Cell type" here stands for each combination of the match columns
   (see step 4). A well needs at least one other well of its test condition to be scored,
   so a min_wells of 1 is treated as 2. A test condition with no wells left is not scored.
4. Computes each well's average precision (AP) with copairs. A well's AP is 1 when the other
   wells of its test condition (positives) are all more similar to it than any reference well
   (negatives) is, and lower the more often a reference well is more similar than a positive.
   Wells are compared only with wells that share their match column values (e.g. the same
   cell type). The match columns are the columns that define the analysis group, minus the
   reference column.
5. Computes each test condition's mAP (the mean of its wells' AP) and
   a p-value from a copairs null distribution, plus a BH-corrected p-value for the group.
   A test condition is called as active when its corrected p-value is below fdr_threshold.

Writes one row per scored test condition, with its counts, mAP, p-values, and the active
call, and a JSON summary. Groups with no profiles (skipped by reference normalization), no
complete features, no reference wells, or no test condition with enough wells are skipped:
the table has no rows.
"""

import json
import sys
from importlib.metadata import version

import numpy as np
import pandas as pd
from copairs.map import average_precision, mean_average_precision
from copairs.matching import assign_reference_index

PLATE_COLUMN = "Metadata_Plate"
# Temporary column: each reference well gets its own index, and other wells get -1,
# so reference wells are never positives of each other or of the test conditions' wells
REFERENCE_INDEX = "Metadata__reference_index"
NON_REFERENCE = -1
# Temporary column: 1 for reference wells and 0 for other wells. Negatives must differ in it,
# so every negative pair is one test condition well and one reference well
REFERENCE_FLAG = "Metadata__is_reference"
# A test condition needs at least two wells in a cell type: its wells' positives are each other
MIN_SCORABLE_WELLS = 2
NULL_METHODS = ("permutation", "copied")
# The value pycytominer_annotate.py writes for a blank platemap cell
MISSING_VALUE = "_NA_"


def validate_params(
    null_method: str, null_size: int, fdr_threshold: float, seed: int, min_wells: int
):
    """
    Validate the scoring parameters before loading any data, so bad params fail fast with a clear message.
    The distance metric is checked by copairs.
    """
    if null_method not in NULL_METHODS:
        raise ValueError(
            f"null must be one of {', '.join(NULL_METHODS)}, got '{null_method}'"
        )
    if null_size < 1:
        raise ValueError(f"null_size must be >= 1, got {null_size}")
    if not 0.0 < fdr_threshold < 1.0:
        raise ValueError(f"fdr_threshold must be between 0 and 1, got {fdr_threshold}")
    if seed < 0:
        raise ValueError(f"seed must be >= 0, got {seed}")
    if min_wells < 1:
        raise ValueError(f"min_wells must be >= 1, got {min_wells}")


def write_versions(task_process: str):
    with open("versions.yml", "w") as f:
        f.write(f'"{task_process}":\\n')
        f.write(f"    python: {sys.version.split()[0]}\\n")
        f.write(f"    pandas: {version('pandas')}\\n")
        f.write(f"    copairs: {version('copairs')}\\n")


def get_match_columns(
    grouping_column: str | None,
    subgroup_columns: list[str],
    comparison_column: str,
    reference_column: str,
) -> list[str]:
    """
    Columns whose values a well must share with its positive and negative wells.

    These are the grouping, subgroup, and comparison columns, minus plate (wells are compared
    across plates) and the reference column (where reference wells differ from test condition
    wells by definition). Each column is listed once, in order.
    """
    context_columns = [grouping_column, *subgroup_columns, comparison_column]
    return [
        col
        for col in dict.fromkeys(context_columns)
        if col is not None and col != PLATE_COLUMN and col != reference_column
    ]


def empty_activity_table(comparison_column: str) -> pd.DataFrame:
    """The activity table with no rows, for skipped groups."""
    return pd.DataFrame(
        {
            comparison_column: pd.Series(dtype="string"),
            "n_wells": pd.Series(dtype="int64"),
            "n_plates": pd.Series(dtype="int64"),
            "n_reference_wells": pd.Series(dtype="int64"),
            "mean_average_precision": pd.Series(dtype="float64"),
            "p_value": pd.Series(dtype="float64"),
            "corrected_p_value": pd.Series(dtype="float64"),
            "active": pd.Series(dtype="bool"),
        }
    )


def write_summary(
    summary: dict,
    n_test_conditions_scored: int,
    n_test_conditions_active: int,
    skip_reason: str | None,
    group_tag: str,
):
    """
    Write phenotypic_activity_summary_{group_tag}.json: summary, plus the test condition counts and skip status.
    summary is not modified.
    """
    output_summary = {
        **summary,
        "n_test_conditions_scored": n_test_conditions_scored,
        "n_test_conditions_active": n_test_conditions_active,
        "skipped": skip_reason is not None,
        "skip_reason": skip_reason,
    }
    with open(f"phenotypic_activity_summary_{group_tag}.json", "w") as f:
        json.dump(output_summary, f, indent=2)


def write_skipped(
    summary: dict, comparison_column: str, skip_reason: str, group_tag: str
):
    """Write an activity table with no rows and the summary of a skipped group, and warn on stderr."""
    print(f"WARNING: skipped: {skip_reason}", file=sys.stderr)
    empty_activity_table(comparison_column).to_parquet(
        f"phenotypic_activity_{group_tag}.parquet", index=False
    )
    write_summary(
        summary=summary,
        n_test_conditions_scored=0,
        n_test_conditions_active=0,
        skip_reason=skip_reason,
        group_tag=group_tag,
    )


def reference_wells_per_test_condition(
    neg_pairs: np.ndarray, test_conditions: pd.Series, is_reference: pd.Series
) -> pd.Series:
    """
    Number of distinct reference wells used as negatives by each test condition's wells.

    neg_pairs is copairs' (n, 2) array of negative pairs. Each pair holds two row positions in
    test_conditions and is_reference, in no particular order.
    """
    is_reference_array = is_reference.to_numpy()
    first_well = neg_pairs[:, 0]
    second_well = neg_pairs[:, 1]
    first_is_reference = is_reference_array[first_well]
    second_is_reference = is_reference_array[second_well]

    # Keep pairs of one test condition well and one reference well. Negatives are required to differ in
    # reference status, so this keeps every pair; it guards the counts if that requirement changes.
    one_reference = first_is_reference != second_is_reference
    first_well = first_well[one_reference]
    second_well = second_well[one_reference]
    first_is_reference = first_is_reference[one_reference]

    # Put each pair in the same order: the test condition well, then the reference well.
    condition_well = np.where(first_is_reference, second_well, first_well)
    reference_well = np.where(first_is_reference, first_well, second_well)

    # Count the distinct reference wells paired with each test condition's wells.
    condition_pairs = pd.DataFrame(
        {
            "test_condition": test_conditions.to_numpy()[condition_well],
            "reference_well": reference_well,
        }
    )
    return condition_pairs.groupby("test_condition")["reference_well"].nunique()


def main():
    # ---- Capture Nextflow variables ----------------------------------------
    profiles_path = "${profiles}"
    features_file = "${features_file}"
    # raw string so JSON escapes in metadata values survive
    meta = json.loads(r"""${meta_json}""")
    options = json.loads(r"""${options_json}""")
    distance = options["distance_metric"]
    null_method = options["null_method"]
    null_size = int(options["null_size"])
    fdr_threshold = float(options["fdr_threshold"])
    seed = int(options["seed"])
    min_wells = int(options["min_wells"])
    task_process = "${task.process}"

    validate_params(
        null_method=null_method,
        null_size=null_size,
        fdr_threshold=fdr_threshold,
        seed=seed,
        min_wells=min_wells,
    )
    # written up front so skipped groups report versions too
    write_versions(task_process)

    group_tag = meta["group_tag"]
    activity_path = f"phenotypic_activity_{group_tag}.parquet"
    comparison_column = meta["comparison_column"]
    reference_column = meta["reference_column"]
    reference_values = meta["reference_values"]
    match_by = get_match_columns(
        grouping_column=meta["grouping_column"],
        subgroup_columns=list(meta["subgroup_values"].keys()),
        comparison_column=comparison_column,
        reference_column=reference_column,
    )
    # columns a test condition's wells are split by before scoring: within a test condition, only wells that share
    # these values are positives of each other (the comparison column, then the other match columns)
    condition_set_by = list(dict.fromkeys([comparison_column, *match_by]))
    # positives: wells of the same test condition that share the match columns, excluding reference wells
    # (each reference well has its own reference index)
    pos_sameby = [*condition_set_by, REFERENCE_INDEX]
    # negatives: a test condition's wells paired with the reference wells that share the match columns.
    # Requiring the reference flag to differ leaves out pairs of two test condition wells and pairs of two
    # reference wells (whose APs are not tested), whatever the reference and comparison columns are.
    neg_sameby = match_by
    neg_diffby = [REFERENCE_FLAG]
    # a test condition needs at least two wells in a cell type to have positive pairs, whatever min_wells is
    effective_min_wells = max(min_wells, MIN_SCORABLE_WELLS)

    summary = {
        "group_tag": group_tag,
        "aggregate_level": meta["aggregate_level"],
        "grouping_name": meta["grouping_name"],
        "grouping_column": meta["grouping_column"],
        "group": meta["group"],
        "subgroup_values": meta["subgroup_values"],
        "comparison_column": comparison_column,
        "reference_column": reference_column,
        "reference_values": reference_values,
        "ap_distance_metric": distance,
        "map_null_method": null_method,
        "map_null_size": null_size,
        "map_fdr_threshold": fdr_threshold,
        "map_null_seed": seed,
        "min_wells_per_set": effective_min_wells,
        "copairs_version": version("copairs"),
        "positive_match_columns": condition_set_by,
        "negative_match_columns": neg_sameby,
        "reference_normalization_method": meta.get("reference_normalization_method"),
        "plate_normalization_method": meta.get("plate_normalization_method"),
        "plate_normalization_reference_profiles": meta.get(
            "plate_normalization_reference_profiles"
        ),
        "edge_wells_included": meta.get("edge_wells_included"),
    }

    # ---- Load profiles -------------------------------------------------------
    with open(features_file) as fh:
        features = [line.strip() for line in fh if line.strip()]
    df = pd.read_parquet(profiles_path, dtype_backend="pyarrow")
    summary["n_wells_input"] = len(df)
    summary["n_features_input"] = len(features)
    if not features or len(df) == 0:
        summary["features_dropped_nan"] = []
        summary["n_wells_dropped"] = 0
        write_skipped(
            summary=summary,
            comparison_column=comparison_column,
            skip_reason="no reference-normalized profiles",
            group_tag=group_tag,
        )
        return

    metadata_cols = [c for c in df.columns if c.startswith("Metadata_")]
    missing = [
        col
        for col in dict.fromkeys(
            [
                comparison_column,
                PLATE_COLUMN,
                *match_by,
                reference_column,
            ]
        )
        if col not in metadata_cols
    ]
    if missing:
        raise ValueError(f"Metadata columns missing from the input profiles: {missing}")
    missing_features = [f for f in features if f not in df.columns]
    if missing_features:
        raise ValueError(
            f"Features listed in {features_file} are missing from the input profiles: {missing_features}"
        )
    # reduce the dataframe to only metadata columns and features
    df = df[metadata_cols + features].reset_index(drop=True)
    df[features] = df[features].astype("float64")

    # ---- Drop features with any NaN -----------------------------------------
    has_nan = df[features].isna().any()
    dropped_features = [f for f in features if has_nan[f]]
    kept_features = [f for f in features if not has_nan[f]]
    summary["features_dropped_nan"] = dropped_features
    summary["n_features_scored"] = len(kept_features)
    if not kept_features:
        summary["n_wells_dropped"] = 0
        write_skipped(
            summary=summary,
            comparison_column=comparison_column,
            skip_reason="no features without missing values",
            group_tag=group_tag,
        )
        return

    # ---- Identify reference wells -------------------------------------------
    # A reference well's reference_column value is one of the reference_values.
    # Values are compared as text, as in reference normalization.
    is_reference = (
        df[reference_column].astype(str).isin([str(v) for v in reference_values])
    )
    is_reference = is_reference.astype(bool)

    # ---- Drop wells that can't be scored -------------------------------------
    # non-reference wells with no comparison column value (null or blank in the platemap) can't be scored
    no_comparison_value = df[comparison_column].isna() | (
        df[comparison_column].astype(str) == MISSING_VALUE
    )
    no_test_condition = ~is_reference & no_comparison_value
    # A test condition's wells are scored only against the other wells that share the match columns (e.g.,
    # the same cell type), so each such set of wells needs at least effective_min_wells wells.
    # Small sets are dropped, and a test condition whose sets are all small is dropped with them.
    condition_wells = df[~is_reference & ~no_test_condition]
    set_sizes = condition_wells.groupby(condition_set_by, dropna=False)[
        PLATE_COLUMN
    ].transform("size")
    in_small_set = (set_sizes < effective_min_wells).reindex(df.index, fill_value=False)
    keep_wells = ~no_test_condition & ~in_small_set
    summary["n_wells_dropped"] = int((~keep_wells).sum())
    df = df[keep_wells].reset_index(drop=True)
    is_reference = is_reference[keep_wells].reset_index(drop=True)
    summary["n_wells_scored"] = int((~is_reference).sum())
    summary["n_reference_wells"] = int(is_reference.sum())
    # reference normalization skips groups without reference wells, so this only happens when the
    # module runs on its own; without reference wells there are no negatives to score against
    if summary["n_reference_wells"] == 0:
        write_skipped(
            summary=summary,
            comparison_column=comparison_column,
            skip_reason=f"no reference wells with values in {reference_values} for {reference_column}",
            group_tag=group_tag,
        )
        return
    if summary["n_wells_scored"] == 0:
        write_skipped(
            summary=summary,
            comparison_column=comparison_column,
            skip_reason=f"no test condition has at least {effective_min_wells} wells with the same values of {condition_set_by}",
            group_tag=group_tag,
        )
        return

    # ---- Average precision of each well --------------------------------------
    pair_meta = df[metadata_cols].copy()
    pair_meta = assign_reference_index(
        pair_meta,
        condition=pair_meta.index[is_reference],
        reference_col=REFERENCE_INDEX,
        default_value=NON_REFERENCE,
    )
    pair_meta[REFERENCE_FLAG] = is_reference.astype(int).to_numpy()
    ap_scores, pairs = average_precision(
        meta=pair_meta,
        feats=df[kept_features].to_numpy(),
        pos_sameby=pos_sameby,
        pos_diffby=[],
        neg_sameby=neg_sameby,
        neg_diffby=neg_diffby,
        distance=distance,
        progress_bar=False,
        return_pairs=True,
    )
    n_reference_wells = reference_wells_per_test_condition(
        neg_pairs=pairs["neg"],
        test_conditions=df[comparison_column],
        is_reference=is_reference,
    )
    # the reference wells' APs are not tested
    ap_scores = ap_scores[ap_scores[REFERENCE_INDEX] == NON_REFERENCE]

    # ---- mAP and significance of each test condition -----------------------
    map_scores = mean_average_precision(
        ap_scores,
        sameby=[comparison_column],
        null_size=null_size,
        threshold=fdr_threshold,
        seed=seed,
        progress_bar=False,
        null=null_method,
    )

    # ---- Counts per test condition, and the activity table -----------------
    condition_wells = df[~is_reference]
    counts = condition_wells.groupby(comparison_column).agg(
        n_wells=(PLATE_COLUMN, "size"), n_plates=(PLATE_COLUMN, "nunique")
    )
    counts["n_reference_wells"] = n_reference_wells.reindex(counts.index, fill_value=0)
    activity = map_scores.merge(
        counts, left_on=comparison_column, right_index=True, how="left"
    )
    activity["active"] = activity["below_corrected_p"].astype(bool)
    activity = activity[
        [
            comparison_column,
            "n_wells",
            "n_plates",
            "n_reference_wells",
            "mean_average_precision",
            "p_value",
            "corrected_p_value",
            "active",
        ]
    ]
    activity = activity.astype(
        {"n_wells": "int64", "n_plates": "int64", "n_reference_wells": "int64"}
    )
    activity = activity.sort_values(comparison_column).reset_index(drop=True)
    activity.to_parquet(activity_path, index=False)
    write_summary(
        summary=summary,
        n_test_conditions_scored=len(activity),
        n_test_conditions_active=int(activity["active"].sum()),
        skip_reason=None,
        group_tag=group_tag,
    )


if __name__ == "__main__":
    main()
