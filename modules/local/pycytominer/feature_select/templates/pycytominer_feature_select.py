#!/usr/bin/env python3
"""
Nextflow template script to select features using pycytominer.

Reads the parquet files staged in input_parquet/, applies
pycytominer.feature_select() with samples="all", and writes a text file listing
the kept morphological feature columns (one per line; Metadata_* columns are
not included — they are identified downstream by prefix).

When per_channel_correlation is true and correlation_threshold is among the
operations, the feature selection is done in two steps:
All non-correlation operations are run first, followed by the correlation_threshold
operation which is run on each channel group separately.
This avoids deduplication of features that are correlated across channels, which are
more likely to be biologically meaningful.
"""

import json
import re
import sys
import warnings
from collections import defaultdict
from importlib.metadata import version

import pandas as pd
import pycytominer

warnings.filterwarnings("ignore", category=pd.errors.PerformanceWarning)


def write_versions(task_process: str):
    with open("versions.yml", "w") as f:
        f.write(f'"{task_process}":\\n')
        f.write(f"    python: {sys.version.split()[0]}\\n")
        f.write(f"    pycytominer: {version('pycytominer')}\\n")
        f.write(f"    pandas: {version('pandas')}\\n")
        f.write(f"    pyarrow: {version('pyarrow')}\\n")


# pycytominer operations whose results are unreliable or uncomputable when there are few rows.
STATS_OPS = frozenset(
    {
        "variance_threshold",
        "correlation_threshold",
        "drop_outliers",
        "noise_removal",
    }
)

# Sentinel key for features with no defined dye channel.
NO_CHANNEL = tuple()


def discover_channels(feature_names) -> tuple[frozenset[str], frozenset[str]]:
    """
    Discover imaging channels from CellProfiler feature names.

    Returns (primary, derived) frozensets.

    - Primary channels come from Intensity features, because these are present for
      all channels, and directly computed from the images. They follow the pattern
      (<Comp>_Intensity_<Subtype>_<Channel...>).
    - Derived channels come from RadialDistribution features
      (<Comp>_RadialDistribution_<Subtype>_<Channel...>_<...>); any channel
      seen there but not in Intensity is a derived measurement layered on top
      of a primary channel (e.g. 'mito_tubeness' is a tubeness-filtered
      version of the Mito channel).

    Note that some features can include underscores, so we can't always just select a
    single token based on underscore splitting alone.
    Zernike RadialDistribution features are skipped because they follow a different
    naming pattern (and we should not need them to get all the features).
    """
    primary = set()
    radial = set()
    for name in feature_names:
        parts = name.split("_")
        if len(parts) >= 4 and parts[1] == "Intensity":
            primary.add("_".join(parts[3:]))
        elif (
            len(parts) >= 5
            and parts[1] == "RadialDistribution"
            and not parts[2].startswith("Zernike")
        ):
            radial.add("_".join(parts[3:-1]))
    derived = radial - primary
    return frozenset(primary), frozenset(derived)


def build_channel_pattern(channels) -> re.Pattern | None:
    """
    Compile a regex that matches any underscore-bounded channel name.

    Channels are given in longest-first order so multi-token names
    (e.g. 'Brightfield_H') aren't shadowed by their single-token prefixes.
    Returns None when channels is empty.
    """
    if not channels:
        return None
    return re.compile(
        r"(?<=_)("
        + "|".join(re.escape(c) for c in sorted(channels, key=lambda c: (-len(c), c)))
        + r")(?=_)"
    )


def parse_channels(feature_name: str, channel_pattern) -> tuple[str, ...]:
    """
    Return a sorted tuple of channels found in a feature name, or NO_CHANNEL
    when no channel tokens are present or the pattern is None.
    """
    if channel_pattern is None:
        return NO_CHANNEL
    padded = "_" + feature_name + "_"
    found = {m.group(1) for m in channel_pattern.finditer(padded)}
    if not found:
        return NO_CHANNEL
    return tuple(sorted(found))


def group_features_by_channel(
    features, channel_pattern
) -> dict[tuple[str, ...], list[str]]:
    """
    Group features by their channel sets, as identified by parse_channels.
    Returns a dict mapping channel tuples to lists of features.

    Features with no detected channels are grouped together under the
    NO_CHANNEL key.
    """
    groups = defaultdict(list)
    for f in features:
        groups[parse_channels(f, channel_pattern)].append(f)
    return dict(groups)


def apply_per_channel_correlation_threshold(
    df: pd.DataFrame, features: list, channel_pattern, threshold: float
) -> tuple[list[str], list[dict]]:
    """Run pycytominer's correlation_threshold once per channel group.
    Returns (kept_features, per_group_stats) where per_group_stats is a list
    of dicts with one entry per channel group (for the summary JSON).
    """
    channel_groups = group_features_by_channel(features, channel_pattern)
    kept_features = []
    per_group_stats = []
    for channels, group_features in sorted(channel_groups.items()):
        if len(group_features) <= 1:
            # nothing to correlate against — keep as-is
            group_kept = list(group_features)
        else:
            # Run pycytominer's correlation_threshold on this channel group only
            result_df = pycytominer.feature_select(
                profiles=df,
                features=group_features,
                samples="all",
                operation=["correlation_threshold"],
                corr_threshold=threshold,
                corr_method="pearson",
            )
            kept_set = set(result_df.columns)
            group_kept = [f for f in group_features if f in kept_set]
        kept_features.extend(group_kept)
        per_group_stats.append(
            {
                "channels": list(channels),
                "n_features_in": len(group_features),
                "n_features_out": len(group_kept),
            }
        )
    return kept_features, per_group_stats


def main():
    parquet_dir = "input_parquet"
    group_tag = "${group_tag}"
    # raw string so JSON escapes in metadata values survive
    group_meta = json.loads(r"""${meta_json}""")
    operations = [op.strip() for op in "${operations}".split(",")]
    na_cutoff = float("${na_cutoff}")
    corr_threshold = float("${corr_threshold}")
    corr_method = "${corr_method}"
    freq_cut = float("${freq_cut}")
    unique_cut = float("${unique_cut}")
    min_rows_stats = int("${min_rows_stats}")
    outlier_cutoff = float("${outlier_cutoff}")
    noise_removal_perturb_groups = "${noise_removal_perturb_groups}"
    noise_removal_stdev_cutoff = float("${noise_removal_stdev_cutoff}")
    use_per_channel_correlation = "${per_channel_correlation}".lower() == "true"
    task_process = "${task.process}"

    df = pd.read_parquet(parquet_dir, dtype_backend="pyarrow")

    feature_cols = [
        c
        for c in df.columns
        if not c.startswith("Metadata_") and pd.api.types.is_numeric_dtype(df[c])
    ]
    df[feature_cols] = df[feature_cols].astype("float64")

    # Discover imaging channels from feature names.
    primary_channels, derived_channels = discover_channels(feature_cols)
    channel_pattern = build_channel_pattern(primary_channels | derived_channels)
    print(
        f"pycytominer_feature_select: discovered {len(primary_channels)} primary "
        f"channel(s) {sorted(primary_channels)} and {len(derived_channels)} "
        f"derived channel(s) {sorted(derived_channels)}.",
        file=sys.stderr,
    )

    # Skip stats-based operations if there are too few rows to compute them reliably.
    n_rows = df.shape[0]
    if n_rows < min_rows_stats:
        effective_ops = [op for op in operations if op not in STATS_OPS]
        dropped_ops = [op for op in operations if op in STATS_OPS]
        if dropped_ops:
            print(
                f"pycytominer_feature_select: only {n_rows} rows "
                f"(< min_rows_stats={min_rows_stats}); "
                f"skipping statistics-based operations {dropped_ops}.",
                file=sys.stderr,
            )
    else:
        effective_ops = operations

    if (
        "noise_removal" in effective_ops
        and noise_removal_perturb_groups not in df.columns
    ):
        raise ValueError(
            f"pycytominer_feature_select: noise_removal perturb-groups column "
            f"'{noise_removal_perturb_groups}' is not present in the input "
            f"parquet."
        )

    # When use_per_channel_correlation is on, defer correlation_threshold until after
    # the rest of the operations have filtered the feature set.
    do_per_channel = (
        use_per_channel_correlation and "correlation_threshold" in effective_ops
    )
    if do_per_channel:
        first_pass_ops = [op for op in effective_ops if op != "correlation_threshold"]
    else:
        first_pass_ops = effective_ops

    if first_pass_ops:
        selected_df = pycytominer.feature_select(
            profiles=df,
            features="infer",
            samples="all",
            operation=first_pass_ops,
            na_cutoff=na_cutoff,
            corr_threshold=corr_threshold,
            corr_method=corr_method,
            freq_cut=freq_cut,
            unique_cut=unique_cut,
            outlier_cutoff=outlier_cutoff,
            noise_removal_perturb_groups=noise_removal_perturb_groups,
            noise_removal_stdev_cutoff=noise_removal_stdev_cutoff,
        )
        surviving_features = [c for c in feature_cols if c in set(selected_df.columns)]
    else:
        # No operations to apply (e.g., all configured ops were stats ops and N is small).
        # Keep all features so downstream steps can still run.
        selected_df = df
        surviving_features = list(feature_cols)

    per_channel_stats = None
    if do_per_channel:
        selected_feature_cols, per_channel_stats = (
            apply_per_channel_correlation_threshold(
                df=selected_df,
                features=surviving_features,
                channel_pattern=channel_pattern,
                threshold=corr_threshold,
            )
        )
        print(
            f"pycytominer_feature_select: per-channel correlation_threshold "
            f"over {len(per_channel_stats)} channel groups; "
            f"{len(surviving_features)} -> {len(selected_feature_cols)} features.",
            file=sys.stderr,
        )
    else:
        selected_feature_cols = surviving_features

    n_features_in = len(feature_cols)
    n_features_out = len(selected_feature_cols)
    print(
        f"pycytominer_feature_select: {n_features_in} features in, "
        f"{n_features_out} features out "
        f"({n_features_in - n_features_out} removed) "
        f"using operations: {effective_ops} "
        f"(per_channel_correlation={do_per_channel}).",
        file=sys.stderr,
    )

    with open(f"features_{group_tag}.txt", "w") as f:
        for name in selected_feature_cols:
            f.write(f"{name}\\n")

    summary = {
        "group_tag": group_tag,
        "aggregate_level": group_meta["aggregate_level"],
        "grouping_name": group_meta["grouping_name"],
        "grouping_column": group_meta["grouping_column"],
        "group": group_meta["group"],
        "subgroup_values": group_meta["subgroup_values"],
        "operations_requested": operations,
        "operations_applied": effective_ops,
        "per_channel_correlation": do_per_channel,
        "primary_channels": sorted(primary_channels),
        "derived_channels": sorted(derived_channels),
        "n_rows": n_rows,
        "min_rows_stats": min_rows_stats,
        "na_cutoff": na_cutoff,
        "corr_threshold": corr_threshold,
        "corr_method": corr_method,
        "freq_cut": freq_cut,
        "unique_cut": unique_cut,
        "outlier_cutoff": outlier_cutoff,
        "noise_removal_perturb_groups": noise_removal_perturb_groups,
        "noise_removal_stdev_cutoff": noise_removal_stdev_cutoff,
        "n_features_input": n_features_in,
        "n_features_output": n_features_out,
        "n_features_removed": n_features_in - n_features_out,
    }
    if per_channel_stats is not None:
        summary["per_channel_correlation_groups"] = per_channel_stats
    with open(f"feature_select_summary_{group_tag}.json", "w") as f:
        json.dump(summary, f, indent=2)

    write_versions(task_process)


if __name__ == "__main__":
    main()
