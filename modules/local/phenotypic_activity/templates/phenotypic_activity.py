#!/usr/bin/env python3
"""
Nextflow template script to score the phenotypic activity of each compared item in one
analysis group with mean average precision (mAP), using copairs.

A compared item is the set of non-reference wells that share one value of the comparison
column (e.g. one treatment, when comparing treatments within a cell type). An item is active
when its wells are retrieved ahead of the comparison reference wells (e.g. DMSO wells) more
often than expected by chance.

Reads the reference-normalized profiles, keeps the metadata columns and the features listed in
features_file, and then:

1. Drops features with any NaN value (copairs needs complete profiles).
2. Drops non-reference wells with no comparison column value, and compared items with fewer
   than min_wells wells (at least 2, since an item's wells are its positive pairs).
3. Computes the average precision (AP) of every well with copairs, using these pairs:
   - positives: wells of the same item (same comparison column value, not reference wells);
   - negatives: an item's wells paired with reference wells that share the values of the
     match columns: the columns that define the analysis group (grouping, subgroup, and
     comparison columns) that are not reference columns. These are the same columns that
     split the normalization sets in reference normalization, so each well is compared with
     the reference wells it was normalized to (e.g. its own cell type's DMSO wells).
4. Drops the reference wells' AP rows, and computes each item's mAP and its p-value against
   a null distribution (copairs mean_average_precision), with Benjamini-Hochberg correction
   across the items in the group.
   An item is active when its corrected p-value is below fdr_threshold.

Writes one row per scored item, with per-item counts, mAP, p-values, and the active flag, and
a JSON summary. Groups with no profiles (skipped by reference normalization), no complete
features, or no compared item with enough wells are skipped: the table has no rows.
"""

import json
import sys
from importlib.metadata import version

import pandas as pd
from copairs.map import average_precision, mean_average_precision
from copairs.matching import assign_reference_index

PLATE_COLUMN = "Metadata_Plate"
# Temporary column: each reference well gets its own index, and other wells get -1,
# so reference wells are never positives of each other or of the compared items
REFERENCE_INDEX = "Metadata__reference_index"
NON_REFERENCE = -1
# An item needs at least two wells: its wells' positive pairs are each other
MIN_SCORABLE_WELLS = 2
NULL_METHODS = ("permutation", "copied")


def validate_params(
    null_method: str, null_size: int, fdr_threshold: float, seed: int, min_wells: int
):
    """
    Validate the scoring parameters before loading any data, so bad params fail fast with a clear message.
    The distance is checked by copairs.
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


def match_columns(
    grouping_column: str | None,
    subgroup_columns: list[str],
    comparison_column: str,
    reference_columns: list[str],
) -> list[str]:
    """
    Columns whose values an item's well and its negative (reference) wells must share.

    These are the columns that define the analysis group (the grouping column, the subgroup columns,
    and the comparison column) except the reference columns, matching the columns (other than plate)
    that split the normalization sets in reference normalization. Reference columns are left out
    because the reference wells differ from the item's wells in them by definition.
    Columns with a single value in the group add no constraint. Duplicates are listed once, in order.
    """
    context_columns = [grouping_column, *subgroup_columns, comparison_column]
    return [
        col
        for col in dict.fromkeys(context_columns)
        if col is not None and col != PLATE_COLUMN and col not in reference_columns
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
    n_items_scored: int,
    n_items_active: int,
    skip_reason: str | None,
    group_tag: str,
):
    """
    Write phenotypic_activity_summary_{group_tag}.json: summary, plus the item counts and skip status.
    summary is not modified.
    """
    output_summary = {
        **summary,
        "n_items_scored": n_items_scored,
        "n_items_active": n_items_active,
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
        n_items_scored=0,
        n_items_active=0,
        skip_reason=skip_reason,
        group_tag=group_tag,
    )


def reference_wells_per_item(
    neg_pairs, items: pd.Series, is_reference: pd.Series
) -> pd.Series:
    """
    Number of distinct reference wells used as negatives by each item's wells.

    neg_pairs is copairs' (n, 2) array of negative pairs, indexing the rows of items and is_reference.
    A pair is unordered, so each pair is read in both orientations, keeping those from an item's
    well to a reference well.
    """
    pairs = pd.DataFrame(neg_pairs, columns=["first", "second"])
    reversed_pairs = pairs.rename(columns={"first": "second", "second": "first"})
    both_orientations = pd.concat([pairs, reversed_pairs], ignore_index=True)
    from_item = ~is_reference.loc[both_orientations["first"]].to_numpy()
    to_reference = is_reference.loc[both_orientations["second"]].to_numpy()
    item_to_reference = both_orientations[from_item & to_reference]
    item_values = items.loc[item_to_reference["first"]].to_numpy()
    return item_to_reference["second"].groupby(item_values).nunique()


def main():
    # ---- Capture Nextflow variables ----------------------------------------
    profiles_path = "${profiles}"
    features_file = "${features_file}"
    # raw string so JSON escapes in metadata values survive
    meta = json.loads(r"""${meta_json}""")
    distance = "${distance}"
    null_method = "${null_method}"
    null_size = int("${null_size}")
    fdr_threshold = float("${fdr_threshold}")
    seed = int("${seed}")
    min_wells = int("${min_wells}")
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
    comparison_reference = meta["comparison_reference"]
    negatives_match_by = match_columns(
        grouping_column=meta["grouping_column"],
        subgroup_columns=list(meta["subgroup_values"].keys()),
        comparison_column=comparison_column,
        reference_columns=list(comparison_reference.keys()),
    )
    # items whose wells are positives of each other: one per comparison column value, excluding reference wells
    pos_sameby = [comparison_column, REFERENCE_INDEX]
    # negatives: an item's wells paired with the reference wells that share the match columns.
    # When the comparison column is a reference column (e.g. treatments, reference DMSO), it is also
    # in neg_diffby, as in the copairs phenotypic activity recipe.
    neg_sameby = negatives_match_by
    if comparison_column in comparison_reference:
        neg_diffby = [comparison_column, REFERENCE_INDEX]
    else:
        neg_diffby = [REFERENCE_INDEX]
    # an item needs at least two wells to have positive pairs, whatever min_wells is
    effective_min_wells = max(min_wells, MIN_SCORABLE_WELLS)

    summary = {
        "group_tag": group_tag,
        "aggregate_level": meta["aggregate_level"],
        "grouping_name": meta["grouping_name"],
        "grouping_column": meta["grouping_column"],
        "group": meta["group"],
        "subgroup_values": meta["subgroup_values"],
        "comparison_column": comparison_column,
        "comparison_reference": comparison_reference,
        "distance": distance,
        "null": null_method,
        "null_size": null_size,
        "fdr_threshold": fdr_threshold,
        "seed": seed,
        "min_wells": min_wells,
        "copairs_version": version("copairs"),
        "pos_sameby": [comparison_column],
        "neg_sameby": neg_sameby,
        "neg_diffby": [col for col in neg_diffby if col != REFERENCE_INDEX],
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
        summary["items_dropped_too_few_wells"] = {}
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
                *negatives_match_by,
                *comparison_reference,
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
        summary["items_dropped_too_few_wells"] = {}
        write_skipped(
            summary=summary,
            comparison_column=comparison_column,
            skip_reason="no features without missing values",
            group_tag=group_tag,
        )
        return

    # ---- Identify reference wells -------------------------------------------
    # A reference well matches one of the listed values in every comparison_reference column:
    # start with every well, and keep only those that match each column in turn.
    # Values are compared as text, as in reference normalization.
    is_reference = pd.Series(True, index=df.index)
    for col, values in comparison_reference.items():
        matches_column = df[col].astype(str).isin([str(v) for v in values])
        is_reference = is_reference & matches_column
    is_reference = is_reference.astype(bool)

    # ---- Drop wells that can't be scored, and items with too few wells --------
    # non-reference wells with no comparison value belong to no item
    no_item = ~is_reference & df[comparison_column].isna()
    summary["n_wells_dropped_no_comparison_value"] = int(no_item.sum())
    item_sizes = df[~is_reference & ~no_item].groupby(comparison_column).size()
    small_items = item_sizes[item_sizes < effective_min_wells]
    summary["items_dropped_too_few_wells"] = {
        str(item): int(n_wells) for item, n_wells in small_items.items()
    }
    in_small_item = ~is_reference & df[comparison_column].isin(small_items.index)
    keep_wells = ~no_item & ~in_small_item
    df = df[keep_wells].reset_index(drop=True)
    is_reference = is_reference[keep_wells].reset_index(drop=True)
    summary["n_wells_scored"] = int((~is_reference).sum())
    summary["n_reference_wells"] = int(is_reference.sum())
    if summary["n_wells_scored"] == 0:
        write_skipped(
            summary=summary,
            comparison_column=comparison_column,
            skip_reason=f"no compared item has at least {effective_min_wells} wells",
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
    n_reference_wells = reference_wells_per_item(
        neg_pairs=pairs["neg"],
        items=df[comparison_column],
        is_reference=is_reference,
    )
    # the reference wells' APs are not tested
    ap_scores = ap_scores[ap_scores[REFERENCE_INDEX] == NON_REFERENCE]

    # ---- mAP and significance of each item -----------------------------------
    map_scores = mean_average_precision(
        ap_scores,
        sameby=[comparison_column],
        null_size=null_size,
        threshold=fdr_threshold,
        seed=seed,
        progress_bar=False,
        null=null_method,
    )

    # ---- Per-item counts, and the activity table ----------------------------
    item_wells = df[~is_reference]
    counts = item_wells.groupby(comparison_column).agg(
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
        n_items_scored=len(activity),
        n_items_active=int(activity["active"].sum()),
        skip_reason=None,
        group_tag=group_tag,
    )


if __name__ == "__main__":
    main()
