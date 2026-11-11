#!/usr/bin/env python3
"""
Nextflow template script to normalize one analysis group's per-well profiles to
its comparison reference wells (reference normalization), using pycytominer.

Reads the parquet files staged in input_parquet/, keeps the metadata columns and
the features listed in features_file, and then:

1. Identifies reference wells: wells whose value in every comparison_reference
   column is one of that column's listed values (compared as text).
2. Splits the wells into normalization sets: the wells that are normalized
   together, to the reference wells among them. Each plate is a normalization
   set or, when the comparison column is not one of the reference columns, each
   plate and comparison column value (so, e.g., each cell type is normalized to
   its own reference wells). The columns that define the sets are normalize_by.
3. Drops the wells of normalization sets with fewer than MIN_REFERENCE_WELLS
   reference wells (the spread of a single well is zero), and warns about sets
   with fewer than FEW_REFERENCE_WELLS.
4. Drops features with zero spread among the reference wells of any
   normalization set (pycytominer adds a tiny epsilon to the spread instead of
   failing, so these features would otherwise become very large values).
5. Normalizes each normalization set with pycytominer.normalize, fitted on the
   set's reference wells and applied to all of its wells (reference wells
   included).
   NaN values are skipped when fitting and left as NaN in the output.

Writes the normalized profiles (all kept wells, in input order), the list of
kept features (one per line, the same format as pycytominer_feature_select),
a JSON summary, and a warnings file when there is something to warn about.
Groups with no usable reference wells or no features left are skipped: the
profiles parquet has no rows and the features file is empty.
"""

import json
import sys
import warnings
from importlib.metadata import version

import pandas as pd
import pycytominer
from scipy.stats import median_abs_deviation

# pyarrow-backed DataFrames trigger pandas' fragmentation warning; quiet it.
warnings.filterwarnings("ignore", category=pd.errors.PerformanceWarning)

PLATE_COLUMN = "Metadata_Plate"
# Temporary column marking reference wells, used as the pycytominer samples query
REFERENCE_FLAG = "Metadata__reference_well"
# Normalization sets with fewer reference wells than this are dropped: the spread can't be estimated
MIN_REFERENCE_WELLS = 2
# Normalization sets with fewer reference wells than this are kept, with a warning
FEW_REFERENCE_WELLS = 5
METHODS = ("mad_robustize", "standardize", "robustize")


def write_versions(task_process: str):
    with open("versions.yml", "w") as f:
        f.write(f'"{task_process}":\\n')
        f.write(f"    python: {sys.version.split()[0]}\\n")
        f.write(f"    pycytominer: {version('pycytominer')}\\n")
        f.write(f"    pandas: {version('pandas')}\\n")
        f.write(f"    pyarrow: {version('pyarrow')}\\n")


def reference_spread(values: pd.DataFrame, method: str) -> pd.Series:
    """
    Per-feature spread of the reference wells, measured the way the method scales:
    MAD for mad_robustize, standard deviation for standardize, and interquartile range for robustize.
    NaNs are skipped; a feature with no non-NaN values has a NaN spread.
    """
    if method == "mad_robustize":
        spread = median_abs_deviation(values, nan_policy="omit", axis=0)
        return pd.Series(spread, index=values.columns)
    if method == "standardize":
        return values.std(ddof=0)
    if method == "robustize":
        return values.quantile(0.75) - values.quantile(0.25)
    raise ValueError(f"normalize_method must be one of {METHODS}, got '{method}'")


def set_label(normalize_by: list[str], key) -> dict:
    """Map a normalization set's groupby key to a {column: value} dict."""
    if not isinstance(key, tuple):
        key = (key,)
    return {
        col: (None if pd.isna(val) else str(val)) for col, val in zip(normalize_by, key)
    }


def write_summary(
    summary: dict,
    n_wells_output: int,
    n_features_output: int,
    skip_reason: str | None,
    group_tag: str,
):
    """
    Write reference_normalization_summary_{group_tag}.json: summary, plus the output counts and skip status.
    summary is not modified.
    """
    output_summary = {
        **summary,
        "n_wells_output": n_wells_output,
        "n_features_output": n_features_output,
        "skipped": skip_reason is not None,
        "skip_reason": skip_reason,
    }
    with open(f"reference_normalization_summary_{group_tag}.json", "w") as f:
        json.dump(output_summary, f, indent=2)


def write_warnings(messages: list[str], skip_reason: str | None, group_tag: str):
    """
    Write reference_normalization_warnings_{group_tag}.txt: messages, plus the skip reason,
    one per line, and print them to stderr. No file is written if there is nothing to warn about.
    messages is not modified.
    """
    output_messages = list(messages)
    if skip_reason is not None:
        output_messages.append(f"skipped: {skip_reason}")
    if not output_messages:
        return
    with open(f"reference_normalization_warnings_{group_tag}.txt", "w") as f:
        for message in output_messages:
            print(f"WARNING: {message}", file=sys.stderr)
            f.write(f"{message}\\n")


def main():
    # ---- Capture Nextflow variables ----------------------------------------
    parquet_dir = "input_parquet"
    features_file = "${features_file}"
    # raw string so JSON escapes in metadata values survive
    meta = json.loads(r"""${meta_json}""")
    normalize_method = "${normalize_method}"
    task_process = "${task.process}"

    group_tag = meta["group_tag"]
    profiles_path = f"reference_normalized_{group_tag}.parquet"
    features_path = f"reference_normalized_features_{group_tag}.txt"
    comparison_column = meta["comparison_column"]
    comparison_reference = meta["comparison_reference"]

    if normalize_method not in METHODS:
        raise ValueError(
            f"normalize_method must be one of {METHODS}, got '{normalize_method}'"
        )
    # written up front so skipped groups report versions too
    write_versions(task_process)

    # Columns defining the normalization sets: the wells normalized together, to the reference wells among them.
    # Each plate is a set, split by comparison column value when that column does not define the reference.
    match_comparison = comparison_column not in comparison_reference
    normalize_by = [PLATE_COLUMN] + ([comparison_column] if match_comparison else [])

    summary = {
        "group_tag": group_tag,
        "aggregate_level": meta["aggregate_level"],
        "grouping_name": meta["grouping_name"],
        "grouping_column": meta["grouping_column"],
        "group": meta["group"],
        "subgroup_values": meta["subgroup_values"],
        "method": normalize_method,
        "comparison_column": comparison_column,
        "comparison_reference": comparison_reference,
        "normalize_by": normalize_by,
        "min_reference_wells": MIN_REFERENCE_WELLS,
        "plate_normalization_method": meta.get("plate_normalization_method"),
        "plate_normalization_reference_profiles": meta.get(
            "plate_normalization_reference_profiles"
        ),
    }
    messages = []

    # ---- Load profiles -------------------------------------------------------
    with open(features_file) as fh:
        features = [line.strip() for line in fh if line.strip()]
    df = pd.read_parquet(parquet_dir, dtype_backend="pyarrow")
    metadata_cols = [c for c in df.columns if c.startswith("Metadata_")]
    missing = [
        col
        for col in [PLATE_COLUMN, comparison_column, *comparison_reference]
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
    # pycytominer.normalize calls scipy, which doesn't work with nullable types
    df[features] = df[features].astype("float64")
    summary["n_wells_input"] = len(df)
    summary["n_features_input"] = len(features)

    # ---- Identify reference wells -------------------------------------------
    # A reference well matches one of the listed values in every comparison_reference column:
    # start with every well, and keep only those that match each column in turn.
    # Values are compared as text, as platemap values are read.
    is_reference = pd.Series(True, index=df.index)
    for col, values in comparison_reference.items():
        matches_column = df[col].astype(str).isin([str(v) for v in values])
        is_reference = is_reference & matches_column
    df[REFERENCE_FLAG] = is_reference.astype(bool)

    # ---- Normalization sets and their reference wells -----------------------
    normalization_sets = []
    keep_wells = pd.Series(False, index=df.index)
    for key, set_df in df.groupby(normalize_by, dropna=False, sort=True):
        n_reference = int(set_df[REFERENCE_FLAG].sum())
        label = set_label(normalize_by, key)
        normalization_sets.append(
            {**label, "n_wells": len(set_df), "n_reference_wells": n_reference}
        )
        if n_reference < MIN_REFERENCE_WELLS:
            messages.append(
                f"{label}: {n_reference} reference well(s), fewer than {MIN_REFERENCE_WELLS}; "
                f"dropping its {len(set_df)} well(s)."
            )
        else:
            keep_wells[set_df.index] = True
            if n_reference < FEW_REFERENCE_WELLS:
                messages.append(
                    f"{label}: only {n_reference} reference wells; normalization may be noisy."
                )
    summary["normalization_sets"] = normalization_sets
    summary["n_wells_dropped_no_reference"] = int((~keep_wells).sum())
    df = df[keep_wells]
    summary["n_reference_wells"] = int(df[REFERENCE_FLAG].sum())

    if summary["n_reference_wells"] == 0:
        summary["features_dropped_zero_spread"] = []
        # skipped: write a profiles parquet with no rows and an empty features file
        skip_reason = f"no plate has at least {MIN_REFERENCE_WELLS} reference wells"
        df.iloc[0:0].drop(columns=REFERENCE_FLAG).to_parquet(profiles_path, index=False)
        with open(features_path, "w"):
            pass
        write_summary(
            summary=summary,
            n_wells_output=0,
            n_features_output=0,
            skip_reason=skip_reason,
            group_tag=group_tag,
        )
        write_warnings(messages=messages, skip_reason=skip_reason, group_tag=group_tag)
        return

    # ---- Drop features with zero reference spread ---------------------------
    reference_df = df[df[REFERENCE_FLAG]]
    # A feature is dropped if its reference spread is zero in any normalization set:
    # start with no features flagged, and flag those with zero spread in each set in turn.
    zero_spread = pd.Series(False, index=features)
    for _key, set_reference_df in reference_df.groupby(normalize_by, dropna=False):
        set_spread = reference_spread(set_reference_df[features], normalize_method)
        zero_spread = zero_spread | (set_spread == 0)
    dropped_features = [f for f in features if zero_spread[f]]
    kept_features = [f for f in features if not zero_spread[f]]
    summary["features_dropped_zero_spread"] = dropped_features
    if dropped_features:
        messages.append(
            f"dropped {len(dropped_features)} feature(s) with zero spread among the reference wells of a normalization set."
        )
    if not kept_features:
        # skipped: write a profiles parquet with no rows (metadata columns only) and an empty features file
        skip_reason = "no features have nonzero spread among the reference wells"
        df.iloc[0:0][metadata_cols].to_parquet(profiles_path, index=False)
        with open(features_path, "w"):
            pass
        write_summary(
            summary=summary,
            n_wells_output=0,
            n_features_output=0,
            skip_reason=skip_reason,
            group_tag=group_tag,
        )
        write_warnings(messages=messages, skip_reason=skip_reason, group_tag=group_tag)
        return

    # ---- Normalize each set to its reference wells ---------------------------
    meta_features = metadata_cols + [REFERENCE_FLAG]
    normalized_sets = [
        pycytominer.normalize(
            profiles=set_df,
            features=kept_features,
            meta_features=meta_features,
            samples=f"{REFERENCE_FLAG} == True",
            method=normalize_method,
        )
        for _key, set_df in df.groupby(normalize_by, dropna=False)
    ]
    # restore the input row order
    normalized_df = pd.concat(normalized_sets).sort_index()
    normalized_df = normalized_df[meta_features + kept_features]
    normalized_df.drop(columns=REFERENCE_FLAG).to_parquet(profiles_path, index=False)
    with open(features_path, "w") as f:
        f.writelines(f"{name}\\n" for name in kept_features)
    write_summary(
        summary=summary,
        n_wells_output=len(normalized_df),
        n_features_output=len(kept_features),
        skip_reason=None,
        group_tag=group_tag,
    )
    write_warnings(messages=messages, skip_reason=None, group_tag=group_tag)


if __name__ == "__main__":
    main()
