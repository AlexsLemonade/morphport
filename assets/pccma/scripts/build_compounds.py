#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "pandas==2.3.3",
# ]
# ///
"""
Build repo1_compounds.csv, the REPO library annotations for the compounds in the REPO1 screen platemaps.

The output has one row per screen compound, with the library's columns except the chemical structure columns
(SMILES and InChIKey), which are large and not used here.
Columns that hold lists use `|` to separate values (the library uses commas, which also appear within some values):

- MOA: from the long-format MOA table, which has one row per compound and MOA,
  because some MOA names contain commas and cannot be split from the library string.
- Target: from the long-format target table, with one row per compound and target gene.
- Disease Area and Indication: the library strings split on commas, with exact repeats removed.

Before writing, the script checks that:

- each compound ID has only one library row, after removing exact duplicate rows;
- every screen compound is in the library;
- the library MOA and Target strings agree with the long-format tables for every screen compound;
- no MOA or target contains `|`.

The MOA comparison ignores letter case and treats every run of non-ASCII characters as the same placeholder,
because the library has garbled versions of some non-ASCII characters (e.g. `‚Äì` for an en dash).
Values are read and written as text, so they are not reformatted.

Usage, from the repository root:

    uv run assets/pccma/scripts/build_compounds.py
"""

import argparse
import re
from pathlib import Path

import pandas as pd

PCCMA_DIR = Path(__file__).resolve().parent.parent
SOURCE_DIR = PCCMA_DIR / "source" / "repo1"

# Length of a Broad compound ID (e.g. BRD-K75699339), the prefix of a batch ID (e.g. BRD-K75699339-001-12-9)
COMPOUND_ID_LENGTH = 13

# Chemical structure columns, dropped from the output to keep it small
STRUCTURE_COLUMNS = ["SMILES", "InChIKey"]

# Separator for list values in the output
LIST_SEPARATOR = "|"

# A run of consecutive non-ASCII characters, replaced by NON_ASCII_PLACEHOLDER when comparing MOAs
NON_ASCII_RUN = re.compile(r"[^\x00-\x7f]+")
NON_ASCII_PLACEHOLDER = "?"


def screen_compound_ids(platemaps_dir: Path) -> set[str]:
    """
    Return the compound IDs used in the screen platemaps, from the batch IDs in each platemap.

    The platemaps are the files listed in the File_Name column of barcode_platemap.csv in platemaps_dir.
    """
    barcode_platemap = pd.read_csv(
        platemaps_dir / "barcode_platemap.csv", dtype=str, keep_default_na=False
    )
    compound_ids = set()
    for platemap_name in sorted(set(barcode_platemap["File_Name"])):
        platemap = pd.read_csv(
            platemaps_dir / platemap_name, dtype=str, keep_default_na=False
        )
        batch_ids = platemap.loc[platemap["Batch_Id"] != "", "Batch_Id"]
        compound_ids.update(batch_ids.str[:COMPOUND_ID_LENGTH])
    return compound_ids


def split_list(text: str) -> list[str]:
    """Split a comma-separated library string into its values, removing empty values and exact repeats."""
    values = []
    for raw_value in text.split(","):
        value = raw_value.strip()
        if value and value not in values:
            values.append(value)
    return values


def values_by_compound(table: pd.DataFrame, value_column: str) -> dict[str, list[str]]:
    """Return each compound's values from a long-format table, in table order, without exact repeats."""
    values = {}
    for compound_id, value in zip(table["compound_id"], table[value_column]):
        if compound_id not in values:
            values[compound_id] = []
        if value not in values[compound_id]:
            values[compound_id].append(value)
    return values


def normalize_moa(text: str) -> str:
    """Normalize MOA text for comparison: replace non-ASCII runs with a placeholder, lower case, and strip whitespace."""
    return NON_ASCII_RUN.sub(NON_ASCII_PLACEHOLDER, text).lower().strip()


def library_moa_mismatch(library_moa: str, long_moas: list[str]) -> str | None:
    """
    Compare one compound's library MOA string with its long-format MOAs.

    Returns None if the library string is exactly the long-format MOAs (each possibly repeated) separated by commas,
    or a description of the difference otherwise.
    """
    remaining = normalize_moa(library_moa)
    # Remove the longest MOAs first, so a short MOA is never removed from inside a longer one
    normalized_moas = sorted(
        (normalize_moa(moa) for moa in long_moas), key=len, reverse=True
    )
    for moa in normalized_moas:
        if moa not in remaining:
            return f"MOA '{moa}' is not in the library string"
        remaining = remaining.replace(moa, "")
    leftover = remaining.replace(",", "").strip()
    if leftover:
        return f"library string has text not in the long-format MOAs: '{leftover}'"
    return None


def compound_problems(
    compound_id: str,
    library_moa: str,
    library_target: str,
    long_moas: list[str],
    long_targets: list[str],
) -> list[str]:
    """Return the ways one compound's library MOA and Target strings disagree with its long-format values."""
    problems = []
    if long_moas:
        mismatch = library_moa_mismatch(library_moa=library_moa, long_moas=long_moas)
        if mismatch is not None:
            problems.append(f"{compound_id}: {mismatch}")
    elif library_moa != "":
        problems.append(f"{compound_id}: library MOA has no long-format MOAs")
    if set(split_list(library_target)) != set(long_targets):
        problems.append(
            f"{compound_id}: library targets differ from long-format targets"
        )
    for value in long_moas + long_targets:
        if LIST_SEPARATOR in value:
            problems.append(
                f"{compound_id}: value '{value}' contains '{LIST_SEPARATOR}'"
            )
    return problems


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--library",
        type=Path,
        default=SOURCE_DIR / "REPO2025_Broad_Cpd_ID_REPO2025.csv",
        help="source compound library",
    )
    parser.add_argument(
        "--moa",
        type=Path,
        default=SOURCE_DIR / "repo1_moa_metadata_annotation.csv",
        help="source long-format MOA table",
    )
    parser.add_argument(
        "--target",
        type=Path,
        default=SOURCE_DIR / "repo1_target_metadata_annotation.csv",
        help="source long-format target table",
    )
    parser.add_argument(
        "--platemaps-dir",
        type=Path,
        default=SOURCE_DIR / "SK-N-AS",
        help="directory of source screen platemaps, with barcode_platemap.csv",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=PCCMA_DIR / "repo1_compounds.csv",
        help="output compound table",
    )
    args = parser.parse_args()

    library = pd.read_csv(
        args.library, dtype=str, keep_default_na=False
    ).drop_duplicates()
    long_moas = values_by_compound(
        table=pd.read_csv(args.moa, dtype=str, keep_default_na=False),
        value_column="moa",
    )
    long_targets = values_by_compound(
        table=pd.read_csv(args.target, dtype=str, keep_default_na=False),
        value_column="gene_symbol",
    )
    screen_ids = screen_compound_ids(args.platemaps_dir)

    problems = []
    repeated_ids = sorted(
        set(library.loc[library["BROAD_CPD_ID"].duplicated(), "BROAD_CPD_ID"])
    )
    if repeated_ids:
        problems.append(
            f"compounds with conflicting library rows: {', '.join(repeated_ids)}"
        )
    missing_ids = sorted(screen_ids - set(library["BROAD_CPD_ID"]))
    if missing_ids:
        problems.append(
            f"screen compounds not in the library: {', '.join(missing_ids)}"
        )

    compounds = library[library["BROAD_CPD_ID"].isin(screen_ids)].drop(
        columns=STRUCTURE_COLUMNS
    )
    for compound_id, library_moa, library_target in zip(
        compounds["BROAD_CPD_ID"], compounds["MOA"], compounds["Target"]
    ):
        problems.extend(
            compound_problems(
                compound_id=compound_id,
                library_moa=library_moa,
                library_target=library_target,
                long_moas=long_moas.get(compound_id, []),
                long_targets=long_targets.get(compound_id, []),
            )
        )
    if problems:
        raise SystemExit(
            "Problems building the compound table:\n" + "\n".join(problems)
        )

    # Rewrite list columns with LIST_SEPARATOR, taking MOAs and targets from the long-format tables
    compound_ids = list(compounds["BROAD_CPD_ID"])
    compounds["MOA"] = [
        LIST_SEPARATOR.join(long_moas.get(compound_id, []))
        for compound_id in compound_ids
    ]
    compounds["Target"] = [
        LIST_SEPARATOR.join(long_targets.get(compound_id, []))
        for compound_id in compound_ids
    ]
    compounds["Disease Area"] = [
        LIST_SEPARATOR.join(split_list(text)) for text in compounds["Disease Area"]
    ]
    compounds["Indication"] = [
        LIST_SEPARATOR.join(split_list(text)) for text in compounds["Indication"]
    ]

    compounds.to_csv(args.output, index=False)
    print(f"Wrote {len(compounds)} screen compounds to {args.output}")


if __name__ == "__main__":
    main()
