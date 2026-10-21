#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "numpy==2.3.3",
#     "pandas==2.3.3",
#     "pyarrow==22.0.0",
# ]
# ///
"""
Generate synthetic single-cell profiles for the pipeline test data.

The test data are defined by a samplesheet and the platemaps it refers to,
which are tracked in the repository (by default in tests/data/).
For each plate in the samplesheet, this script simulates cells in every well of the plate's platemap,
and writes a converted parquet file to the plate's parquet_path (under --output-dir if relative),
as expected from data in the Cell Painting Gallery at the converted parquet stage (no SQLite processing).
The parquet files are ignored by git (they hold the bulk of the data);
a .gitkeep file is written alongside each one to keep the directory structure.

Platemaps are found as in the pipeline: from the samplesheet platemap_file column, or by looking up
the plate in <platemaps_dir>/<batch>/barcode_platemap.csv and reading
<platemaps_dir>/<batch>/platemap/<Plate_Map_Name>.{txt,tsv,csv}.
Only the well_position column of each platemap is used.
Input paths in the samplesheet are relative to the current directory, as in the pipeline.

Feature values are random, with no structure by cell line or other platemap columns.
Feature names follow CellProfiler conventions, and include the features used by the coSMicQC filters
in assets/cosmicqc_filter_settings.json.
One feature is constant and one has many missing values, to exercise feature selection.
Metadata column names follow the CytoTable cpg0016 JUMP preset used elsewhere in the pipeline
(Metadata_Plate, Metadata_Well, Metadata_ImageNumber, Metadata_ObjectNumber).

Usage (from the repository root):

    uv run scripts/generate_test_data.py

Output is deterministic for a given --seed and set of platemaps.
"""

import argparse
from collections.abc import Mapping
from itertools import pairwise
from pathlib import Path
from typing import cast

import numpy as np
import pandas as pd

# mean cells per well, spread over the sites (images) in each well
CELLS_PER_WELL = 8
SITES_PER_WELL = 3

CHANNELS = ["CorrDNA", "CorrER", "CorrAGP", "CorrMito", "CorrRNA"]
COMPARTMENTS = ["Nuclei", "Cells", "Cytoplasm"]
MEASUREMENTS = [
    "AreaShape_Area",
    "AreaShape_Perimeter",
    "AreaShape_Solidity",
    "AreaShape_Eccentricity",
    *(
        f"Intensity_{m}_{ch}"
        for m in ("IntegratedIntensity", "MeanIntensity", "MassDisplacement")
        for ch in CHANNELS
    ),
    *(f"Texture_Contrast_{ch}_3_00_256" for ch in CHANNELS),
    *(f"Correlation_Correlation_{ch1}_{ch2}" for ch1, ch2 in pairwise(CHANNELS)),
]
FEATURES = [
    f"{comp}_{measurement}" for comp in COMPARTMENTS for measurement in MEASUREMENTS
]
# features to exercise feature selection: one constant, and one with missing values above the NA cutoff (0.05)
CONSTANT_FEATURE = "Nuclei_AreaShape_Eccentricity"
MISSING_FEATURE = "Cytoplasm_Correlation_Correlation_CorrAGP_CorrMito"
MISSING_RATE = 0.2


def find_platemap(sample: Mapping[str, str]) -> Path:
    """
    Find the platemap file for a samplesheet row, either from platemap_file or by looking up the plate
    in barcode_platemap.csv under platemaps_dir.
    This mirrors resolvePlatemapFromDir in the pipeline.
    """
    if sample.get("platemap_file"):
        return Path(sample["platemap_file"])
    batch_dir = Path(sample["platemaps_dir"]) / sample["batch"]
    barcodes = pd.read_csv(
        batch_dir / "barcode_platemap.csv", dtype=str, encoding="utf-8-sig"
    )
    match = barcodes.loc[
        barcodes["Assay_Plate_Barcode"] == sample["plate"], "Plate_Map_Name"
    ]
    if len(match) != 1:
        raise ValueError(
            f"Plate {sample['plate']} must appear exactly once in {batch_dir / 'barcode_platemap.csv'}"
        )
    candidates = [
        batch_dir / "platemap" / f"{match.iloc[0]}.{ext}"
        for ext in ("txt", "tsv", "csv")
    ]
    found = [f for f in candidates if f.exists()]
    if len(found) != 1:
        raise ValueError(
            f"Expected one platemap file for plate {sample['plate']}, found {len(found)}"
        )
    return found[0]


def read_wells(platemap_file: Path) -> list[str]:
    """
    Read the well positions from a platemap file, as platemapSeparator in the pipeline does:
    a UTF-8 byte order mark is ignored, and .csv files are comma-separated, .tsv/.txt tab-separated.
    """
    if platemap_file.suffix not in (".csv", ".tsv", ".txt"):
        raise ValueError(
            f"Platemap {platemap_file} must have a .csv, .tsv, or .txt extension"
        )
    sep = "," if platemap_file.suffix == ".csv" else "\t"
    platemap = pd.read_csv(platemap_file, sep=sep, dtype=str, encoding="utf-8-sig")
    if "well_position" not in platemap.columns:
        raise ValueError(
            f"Platemap {platemap_file} is missing the well_position column"
        )
    return platemap["well_position"].tolist()


def simulate_plate(
    plate_id: str, wells: list[str], rng: np.random.Generator
) -> pd.DataFrame:
    """Simulate converted single-cell profiles with random features for the wells of one plate."""
    n_cells = np.maximum(1, rng.poisson(CELLS_PER_WELL, len(wells)))
    well_index = np.repeat(np.arange(len(wells)), n_cells)
    site = rng.integers(1, SITES_PER_WELL + 1, len(well_index))
    meta = pd.DataFrame(
        {
            "Metadata_Plate": plate_id,
            "Metadata_Well": np.repeat(wells, n_cells),
            # one image per site in each well
            "Metadata_ImageNumber": well_index * SITES_PER_WELL + site,
            "Image_Metadata_Site": site,
        }
    ).sort_values("Metadata_ImageNumber", kind="stable", ignore_index=True)
    # number the objects within each image
    object_number = meta.groupby("Metadata_ImageNumber").cumcount() + 1
    meta.insert(3, "Metadata_ObjectNumber", object_number)
    n = len(meta)

    features = pd.DataFrame(rng.lognormal(0, 0.5, (n, len(FEATURES))), columns=FEATURES)
    features[CONSTANT_FEATURE] = 1.0
    features.loc[rng.random(n) < MISSING_RATE, MISSING_FEATURE] = np.nan
    for comp in COMPARTMENTS:
        features[f"{comp}_Number_Object_Number"] = object_number
    return pd.concat([meta, features], axis=1)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate synthetic single-cell profiles for the pipeline test data."
    )
    parser.add_argument(
        "--samplesheet",
        type=Path,
        default=Path("tests/data/samplesheet.csv"),
        help="Samplesheet defining the test plates (default: %(default)s)",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("."),
        help="Base directory for relative parquet_path values in the samplesheet (default: current directory)",
    )
    parser.add_argument("--seed", type=int, default=2026, help="Random seed")
    args = parser.parse_args()

    # all samplesheet values are read as strings
    samples = cast(
        list[dict[str, str]],
        pd.read_csv(args.samplesheet, dtype=str, keep_default_na=False).to_dict(
            "records"
        ),
    )
    rng = np.random.default_rng(args.seed)
    for sample in samples:
        plate_id = sample["plate"]
        wells = read_wells(find_platemap(sample))
        profiles = simulate_plate(plate_id, wells, rng)
        parquet_path = args.output_dir / sample["parquet_path"]
        parquet_path.parent.mkdir(parents=True, exist_ok=True)
        profiles.to_parquet(parquet_path, index=False, compression="zstd")
        # the parquet files are not tracked by git, so keep the directory structure with a .gitkeep file
        parquet_path.with_name(".gitkeep").touch()
        print(
            f"{plate_id}: {len(wells)} wells, {len(profiles)} cells, {profiles.shape[1]} columns"
        )


if __name__ == "__main__":
    main()
