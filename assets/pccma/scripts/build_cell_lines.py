#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "pandas==2.3.3",
# ]
# ///
"""
Build cell_lines.csv, with one row of descriptive metadata per PCCMA cell line.

The source table (source/pccma/pccma_cell_line_metadata.csv) has one row per cell line and atlas plate condition,
so a cell line grown under several plate conditions (U2-OS) has several rows.
This script keeps only the columns that describe the cell line itself, dropping the plate condition columns,
and removes the resulting duplicate rows.
It fails if a cell line still has more than one row, which would mean its descriptive values disagree between rows.
Values are read and written as text, so they are not reformatted.

Usage, from the repository root:

    uv run assets/pccma/scripts/build_cell_lines.py
"""

import argparse
from pathlib import Path

import pandas as pd

PCCMA_DIR = Path(__file__).resolve().parent.parent

# Columns that describe the cell line itself; the remaining source columns describe atlas plate conditions
CELL_LINE_COLUMNS = [
    "cell_line_key",
    "cell_line",
    "depmap_id",
    "cancer_type",
    "sex",
    "age",
    "subtype",
    "source",
    "origin",
    "phase_of_disease",
    "approx_doubling_time_observed",
    "cell_line_link",
    "media_formulation",
]


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--input",
        type=Path,
        default=PCCMA_DIR / "source" / "pccma" / "pccma_cell_line_metadata.csv",
        help="source cell line metadata table",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=PCCMA_DIR / "cell_lines.csv",
        help="output cell line table",
    )
    args = parser.parse_args()

    source_table = pd.read_csv(args.input, dtype=str, keep_default_na=False)
    cell_lines = source_table[CELL_LINE_COLUMNS].drop_duplicates()

    repeated_keys = cell_lines.loc[
        cell_lines["cell_line_key"].duplicated(), "cell_line_key"
    ]
    if not repeated_keys.empty:
        raise SystemExit(
            f"Cell lines with conflicting metadata rows: {', '.join(sorted(set(repeated_keys)))}"
        )

    cell_lines.to_csv(args.output, index=False)
    print(f"Wrote {len(cell_lines)} cell lines to {args.output}")


if __name__ == "__main__":
    main()
