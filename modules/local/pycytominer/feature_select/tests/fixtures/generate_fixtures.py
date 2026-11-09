#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "numpy==2.3.3",
#     "polars==1.35.2",
# ]
# ///
"""
Generate the small parquet fixtures for the pycytominer feature_select module tests.

Writes profiles_P1.parquet and profiles_P2.parquet to the directory containing this script:
per-well profiles for plates P1 and P2, 8 wells each (16 rows together, just above the
default min_rows_stats of 15; one plate alone is below it).

Non-feature columns, which must never appear in a selected feature list:

- Metadata_Plate, Metadata_Well, Metadata_treatment (DMSO or drugA, 4 wells each per plate;
  used as the noise_removal perturb groups)
- Image_FileName_DNA: a string column without the Metadata_ prefix

Features use CellProfiler names (pycytominer only infers features prefixed with Cells,
Nuclei, or Cytoplasm, and its default blocklist matches CellProfiler names).
Unless noted, a feature is independent noise with a small spread (SD 0.3), so it passes every
operation with the default parameters.
Each feature below is designed to be removed by exactly one operation:

- Nuclei_Correlation_Manders_DNA_ER: on pycytominer's default blocklist (blocklist)
- Nuclei_AreaShape_Solidity: missing in 2 of the P1 wells (drop_na_columns; 2/16 and 2/8
  are both above the default na_cutoff of 0.05)
- Cells_AreaShape_EulerNumber: constant 1.0 (variance_threshold)
- Cells_AreaShape_Area: about 600 with a small spread (drop_outliers; above the default
  outlier_cutoff of 500, but not noisy within treatments)
- Nuclei_AreaShape_FormFactor: SD 2 within each treatment (noise_removal, with the default
  noise_removal_stdev_cutoff of 0.8)

Correlated pairs (correlation_threshold, default corr_threshold 0.9):

- Cells_Intensity_MeanIntensity_DNA and Nuclei_Intensity_MeanIntensity_DNA: nearly identical,
  in the same channel group (DNA), so one is removed whether or not correlation is per-channel
- Cells_Intensity_MeanIntensity_ER and Cells_Intensity_MeanIntensity_RNA: nearly identical,
  but in different channel groups (ER and RNA), so one is removed only when correlation is
  computed across all features
- Cells_Intensity_MeanIntensity_Mito and Cytoplasm_Intensity_MeanIntensity_Mito: the second
  is exp(8 x) of the first (rescaled), so the Spearman correlation is 1 but the Pearson
  correlation is below 0.9; one is removed only with corr_method spearman

Features for imaging channel discovery:

- Primary channels (from Intensity features): DNA, ER, RNA, Mito, and Brightfield_H
  (Cells_Intensity_MeanIntensity_Brightfield_H, a channel name containing an underscore)
- Derived channel: mito_tubeness (Cells_RadialDistribution_FracAtD_mito_tubeness_1of4)
- Two-channel group: Cells_Correlation_Pearson_DNA_RNA (DNA and RNA)
- No channel: the AreaShape features, including Cells_AreaShape_Eccentricity (plain noise)

Usage (from the repository root):

    uv run modules/local/pycytominer/feature_select/tests/fixtures/generate_fixtures.py
"""

import pathlib

import numpy as np
import polars as pl

FIXTURE_DIR = pathlib.Path(__file__).parent
PLATES = ["P1", "P2"]
WELLS = [f"A{i:02d}" for i in range(1, 9)]
TREATMENTS = ["DMSO"] * 4 + ["drugA"] * 4
N_ROWS = len(PLATES) * len(WELLS)

# Fixed column order; the selected feature lists keep this order (except per-channel correlation,
# which groups features by channel).
FEATURES = [
    "Cells_AreaShape_Area",
    "Cells_AreaShape_Eccentricity",
    "Cells_AreaShape_EulerNumber",
    "Nuclei_AreaShape_FormFactor",
    "Nuclei_AreaShape_Solidity",
    "Cells_Intensity_MeanIntensity_DNA",
    "Nuclei_Intensity_MeanIntensity_DNA",
    "Cells_Intensity_MeanIntensity_ER",
    "Cells_Intensity_MeanIntensity_RNA",
    "Cells_Intensity_MeanIntensity_Mito",
    "Cytoplasm_Intensity_MeanIntensity_Mito",
    "Cells_Intensity_MeanIntensity_Brightfield_H",
    "Cells_RadialDistribution_FracAtD_mito_tubeness_1of4",
    "Cells_Correlation_Pearson_DNA_RNA",
    "Nuclei_Correlation_Manders_DNA_ER",
]


def make_features(rng: np.random.Generator) -> dict[str, np.ndarray]:
    def noise(sd: float = 0.3) -> np.ndarray:
        return rng.normal(scale=sd, size=N_ROWS)

    dna = noise()
    er = noise()
    mito = rng.uniform(size=N_ROWS)

    solidity = noise()
    # rows 2 and 5 are P1 wells A03 and A06
    solidity[[2, 5]] = np.nan

    return {
        "Cells_AreaShape_Area": 600 + noise(),
        "Cells_AreaShape_Eccentricity": noise(),
        "Cells_AreaShape_EulerNumber": np.ones(N_ROWS),
        "Nuclei_AreaShape_FormFactor": noise(sd=2),
        "Nuclei_AreaShape_Solidity": solidity,
        "Cells_Intensity_MeanIntensity_DNA": dna,
        "Nuclei_Intensity_MeanIntensity_DNA": dna + noise(sd=0.01),
        "Cells_Intensity_MeanIntensity_ER": er,
        "Cells_Intensity_MeanIntensity_RNA": er + noise(sd=0.01),
        "Cells_Intensity_MeanIntensity_Mito": mito,
        # monotonic but strongly nonlinear; scaled so the within-treatment spread stays small
        "Cytoplasm_Intensity_MeanIntensity_Mito": np.exp(8 * mito) / 3000,
        "Cells_Intensity_MeanIntensity_Brightfield_H": noise(),
        "Cells_RadialDistribution_FracAtD_mito_tubeness_1of4": noise(),
        "Cells_Correlation_Pearson_DNA_RNA": noise(),
        "Nuclei_Correlation_Manders_DNA_ER": noise(),
    }


def main():
    rng = np.random.default_rng(101)
    features = make_features(rng)
    assert list(features) == FEATURES

    profiles = pl.DataFrame(
        {
            "Metadata_Plate": [plate for plate in PLATES for _ in WELLS],
            "Metadata_Well": WELLS * len(PLATES),
            "Metadata_treatment": TREATMENTS * len(PLATES),
            "Image_FileName_DNA": [
                f"{plate}_{well}_DNA.tiff" for plate in PLATES for well in WELLS
            ],
            **features,
        }
    ).with_columns(
        # store missing values as nulls, as in real profiles
        pl.col(FEATURES).fill_nan(None)
    )

    for plate in PLATES:
        profiles.filter(pl.col("Metadata_Plate") == plate).write_parquet(
            FIXTURE_DIR / f"profiles_{plate}.parquet"
        )


if __name__ == "__main__":
    main()
