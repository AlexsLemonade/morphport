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
Generate simulated single-cell profiles from PCCMA platemaps for the test_full profile.

Each dataset is one project and batch, with plates taken from the PCCMA platemaps in assets/pccma/platemaps/
(built by assets/pccma/scripts/build_platemaps.py; see assets/pccma/README.md).
For each dataset, this script writes files in the Cell Painting Gallery layout under --output-dir:

- <project>/<source>/workspace/profiles/<batch>/<plate>/<plate>_converted.parquet: single-cell profiles
  at the converted parquet stage, for every well in the plate's platemap
- <project>/<source>/workspace/metadata/platemaps/<batch>/: barcode_platemap.csv (limited to the dataset's plates)
  and a copy of each plate's platemap

Datasets:

- atlas: the 14 cell lines of the Atlas batch at 24, 48, and 72 h (one plate per time point)
- (other datasets including drug treatment data will be added later)

Well factor means are simulated first by combining dataset effects (e.g., cell line and time point), a plate offset, and well noise.
Each cell then adds its own noise to its well's mean, and a fixed matrix maps the result to features, plus feature noise.
Features from the same channel share most of their mapping, so they are correlated, as in real data.
Finally, values are transformed to a plausible range for each measurement
(e.g., positive areas, fractions from 0 to 1, correlations from -1 to 1).

Feature names follow CellProfiler conventions, with the PCCMA channel names (see CHANNELS),
so use assets/cosmicqc_filter_settings_pccma.json for coSMicQC filtering.

Random effects are drawn from generators seeded by --seed and a name (e.g. a cell line or plate barcode),
so each effect is the same in every dataset that uses it, and adding a dataset does not change the others.

Usage (from the repository root):

    uv run scripts/generate_full_test_data.py

Output is deterministic for a given --seed and set of platemaps.
"""

import argparse
import zlib
from collections.abc import Callable
from dataclasses import dataclass
from itertools import combinations
from pathlib import Path

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class Dataset:
    """
    A simulated project: one batch of plates.
    Each dataset's well effects are set by the function for its name in WELL_EFFECTS.
    """

    name: str
    project: str
    batch: str
    plates: tuple[str, ...]


# Gallery source within each project, marking the data as simulated
SOURCE = "simulated"

# Mean cells per well (before dataset effects on cell counts), spread over the sites (images) in each well
CELLS_PER_WELL = 100
SITES_PER_WELL = 9

# Latent factor model: number of latent dimensions, and standard deviations of each source of variation
# in latent units (cell noise has standard deviation 1)
N_LATENT = 16
PLATE_SD = 0.1
WELL_SD = 0.1
# Feature-specific noise, added to each feature after mapping from the latent space
FEATURE_NOISE_SD = 0.3
# Weight of the shared channel (or shape) loadings in each feature's loadings, versus its own random loadings
GROUP_LOADING_WEIGHT = 0.8

# Atlas effects, in latent units: each cell line's offset (per latent dimension),
# and the time point effect at 72 h (along a shared direction, scaled by each cell line's growth rate)
CELL_LINE_SD = 0.5
TIME_EFFECT_72H = 1.5
GROWTH_RATE_SD = 0.3

# Imaging channels as named in the PCCMA CellProfiler pipelines (WayScience/pccma, 3.cp_analysis/pipeline/),
# where CorrectIlluminationApply names each corrected image by its bare channel name (e.g. OrigDNA -> DNA).
# These differ from the JUMP (cpg0016) names used elsewhere in the pipeline (e.g. CorrDNA).
# Channels are listed in the order of the pipelines' measurement modules,
# which sets the order of channel pairs in Correlation feature names (e.g. Correlation_Correlation_AGP_DNA).
CHANNELS = ["AGP", "DNA", "ER", "Mito", "RNA"]
COMPARTMENTS = ["Nuclei", "Cells", "Cytoplasm"]
# Compartments with neighbor measurements: the PCCMA CellProfiler pipelines measure neighbors only for Nuclei and Cells
NEIGHBOR_COMPARTMENTS = ["Nuclei", "Cells"]
# Group for features that are not measured in a channel
SHAPE_GROUP = "Shape"

# Simulated datasets
DATASETS = [
    Dataset(
        name="atlas",
        project="pccma-atlas-simulated",
        batch="Atlas",
        plates=("BR00150695", "BR00150696", "BR00150697"),
    ),
]


@dataclass(frozen=True)
class Measurement:
    """
    A CellProfiler measurement, before adding the compartment prefix.
    kind sets how simulated values are transformed: positive (log-normal around center),
    fraction (between 0 and 1), correlation (between -1 and 1), or count (non-negative integer).
    """

    name: str
    group: str
    kind: str
    center: float


@dataclass(frozen=True)
class Feature:
    """A simulated feature: its full name and how its values are generated."""

    name: str
    group: str
    kind: str
    center: float


@dataclass(frozen=True)
class WellEffects:
    """Well effects for one plate (latent means and mean cell counts), in platemap row order."""

    latent_means: np.ndarray  # (wells, N_LATENT)
    mean_cells: np.ndarray  # (wells,)


def channel_measurements() -> list[Measurement]:
    """Return the measurements made in each imaging channel."""
    measurements = []
    for ch in CHANNELS:
        for name, kind, center in [
            ("IntegratedIntensity", "positive", 50.0),
            ("MeanIntensity", "fraction", 0.1),
            ("StdIntensity", "fraction", 0.03),
            ("MedianIntensity", "fraction", 0.1),
            ("MinIntensity", "fraction", 0.02),
            ("MaxIntensity", "fraction", 0.4),
            ("LowerQuartileIntensity", "fraction", 0.06),
            ("UpperQuartileIntensity", "fraction", 0.15),
            ("MassDisplacement", "positive", 1.0),
        ]:
            measurements.append(Measurement(f"Intensity_{name}_{ch}", ch, kind, center))
        for name, kind, center in [
            ("AngularSecondMoment", "fraction", 0.2),
            ("Contrast", "positive", 2.0),
            ("Correlation", "correlation", 0.5),
            ("Entropy", "positive", 5.0),
            ("InverseDifferenceMoment", "fraction", 0.6),
            ("SumAverage", "positive", 20.0),
            ("Variance", "positive", 10.0),
        ]:
            measurements.append(
                Measurement(f"Texture_{name}_{ch}_3_00_256", ch, kind, center)
            )
        for scale in range(1, 5):
            measurements.append(
                Measurement(f"Granularity_{scale}_{ch}", ch, "positive", 1.0)
            )
        for ring in range(1, 5):
            measurements.append(
                Measurement(
                    f"RadialDistribution_FracAtD_{ch}_{ring}of4", ch, "fraction", 0.25
                )
            )
            measurements.append(
                Measurement(
                    f"RadialDistribution_MeanFrac_{ch}_{ring}of4", ch, "positive", 1.0
                )
            )
            measurements.append(
                Measurement(
                    f"RadialDistribution_RadialCV_{ch}_{ring}of4", ch, "positive", 0.2
                )
            )
    # correlation between channel pairs, grouped with the first channel of the pair
    for ch1, ch2 in combinations(CHANNELS, 2):
        measurements.append(
            Measurement(f"Correlation_Correlation_{ch1}_{ch2}", ch1, "correlation", 0.5)
        )
        measurements.append(
            Measurement(f"Correlation_Overlap_{ch1}_{ch2}", ch1, "fraction", 0.8)
        )
    return measurements


def shape_measurements() -> list[Measurement]:
    """Return the shape measurements, which are not made in a channel."""
    measurements = [
        Measurement(f"AreaShape_{name}", SHAPE_GROUP, kind, center)
        for name, kind, center in [
            ("Area", "positive", 300.0),
            ("Perimeter", "positive", 70.0),
            ("MajorAxisLength", "positive", 25.0),
            ("MinorAxisLength", "positive", 15.0),
            ("MaxFeretDiameter", "positive", 28.0),
            ("MinFeretDiameter", "positive", 14.0),
            ("EquivalentDiameter", "positive", 20.0),
            ("Compactness", "positive", 1.2),
            ("FormFactor", "fraction", 0.7),
            ("Solidity", "fraction", 0.9),
            ("Extent", "fraction", 0.7),
            ("Eccentricity", "fraction", 0.6),
        ]
    ]
    for n, m in [(0, 0), (2, 0), (2, 2), (4, 0), (4, 2)]:
        measurements.append(
            Measurement(f"AreaShape_Zernike_{n}_{m}", SHAPE_GROUP, "fraction", 0.1)
        )
    return measurements


def neighbor_measurements() -> list[Measurement]:
    """Return the neighbor measurements, which are not made in a channel."""
    return [
        Measurement("Neighbors_NumberOfNeighbors_Adjacent", SHAPE_GROUP, "count", 3.0),
        Measurement(
            "Neighbors_FirstClosestDistance_Adjacent", SHAPE_GROUP, "positive", 20.0
        ),
        Measurement(
            "Neighbors_SecondClosestDistance_Adjacent", SHAPE_GROUP, "positive", 30.0
        ),
    ]


def build_features() -> list[Feature]:
    """
    Return the simulated features: every shape and channel measurement in every compartment,
    and neighbor measurements in the compartments listed in NEIGHBOR_COMPARTMENTS.
    """
    measurements = shape_measurements() + channel_measurements()
    features = []
    for comp in COMPARTMENTS:
        comp_measurements = measurements
        if comp in NEIGHBOR_COMPARTMENTS:
            comp_measurements = measurements + neighbor_measurements()
        for m in comp_measurements:
            features.append(Feature(f"{comp}_{m.name}", m.group, m.kind, m.center))
    return features


def keyed_rng(seed: int, key: str) -> np.random.Generator:
    """Return a random generator for one named effect, independent of the order effects are drawn in."""
    return np.random.default_rng([seed, zlib.crc32(key.encode())])


def build_loadings(features: list[Feature], seed: int) -> np.ndarray:
    """
    Return the (features, N_LATENT) loading matrix, each feature's loadings combining its group's shared
    loadings with its own; rows are scaled to unit length, so latent values contribute unit variance per cell.
    """
    rng = keyed_rng(seed, "loadings")
    groups = sorted({f.group for f in features})
    group_loadings = {g: rng.normal(0, 1, N_LATENT) for g in groups}
    shared = np.array([group_loadings[f.group] for f in features])
    own = rng.normal(0, 1, (len(features), N_LATENT))
    loadings = GROUP_LOADING_WEIGHT * shared + (1 - GROUP_LOADING_WEIGHT) * own
    return loadings / np.linalg.norm(loadings, axis=1, keepdims=True)


def transform_values(z: np.ndarray, features: list[Feature]) -> np.ndarray:
    """Transform simulated standardized values (cells, features) to each feature's range."""
    values = np.empty_like(z)
    for j, feature in enumerate(features):
        if feature.kind == "positive":
            values[:, j] = feature.center * np.exp(0.3 * z[:, j])
        elif feature.kind == "fraction":
            logit_center = np.log(feature.center / (1 - feature.center))
            values[:, j] = 1 / (1 + np.exp(-(logit_center + 0.5 * z[:, j])))
        elif feature.kind == "correlation":
            values[:, j] = np.tanh(np.arctanh(feature.center) + 0.5 * z[:, j])
        elif feature.kind == "count":
            values[:, j] = np.round(feature.center * np.exp(0.3 * z[:, j]))
        else:
            raise ValueError(f"Unknown feature kind for {feature.name}: {feature.kind}")
    return values


def atlas_well_effects(platemap: pd.DataFrame, seed: int) -> WellEffects:
    """
    Set atlas well effects: each cell line has its own latent offset, and the time point effect grows linearly
    from none at 24 h to TIME_EFFECT_72H at 72 h along a shared direction, scaled by each cell line's growth rate.
    Cell counts grow with time, from 0.75 to 1.25 times CELLS_PER_WELL.
    """
    time_direction = keyed_rng(seed, "atlas:time").normal(0, 1, N_LATENT)
    time_direction = time_direction / np.linalg.norm(time_direction)
    latent_means = np.zeros((len(platemap), N_LATENT))
    mean_cells = np.zeros(len(platemap))
    for i, (cell_line, time_point) in enumerate(
        zip(platemap["Cell_Line_Name"], platemap["time_point"].astype(int))
    ):
        cell_line_rng = keyed_rng(seed, f"cell_line:{cell_line}")
        cell_line_offset = cell_line_rng.normal(0, CELL_LINE_SD, N_LATENT)
        growth_rate = np.exp(cell_line_rng.normal(0, GROWTH_RATE_SD))
        time_fraction = (time_point - 24) / 48
        latent_means[i] = (
            cell_line_offset
            + TIME_EFFECT_72H * growth_rate * time_fraction * time_direction
        )
        mean_cells[i] = CELLS_PER_WELL * (0.75 + 0.5 * time_fraction)
    return WellEffects(latent_means=latent_means, mean_cells=mean_cells)


# Well effects function for each dataset in DATASETS, by dataset name.
# Each takes a plate's platemap and --seed and returns the WellEffects (latent means and cell counts) for its wells.
WELL_EFFECTS: dict[str, Callable[[pd.DataFrame, int], WellEffects]] = {
    "atlas": atlas_well_effects,
}


def read_platemap(platemaps_dir: Path, batch: str, plate: str) -> pd.DataFrame:
    """Read a plate's PCCMA platemap, found through the batch's barcode_platemap.csv."""
    batch_dir = platemaps_dir / batch
    barcodes = pd.read_csv(batch_dir / "barcode_platemap.csv", dtype=str)
    match = barcodes.loc[barcodes["Assay_Plate_Barcode"] == plate, "Plate_Map_Name"]
    if len(match) != 1:
        raise ValueError(
            f"Plate {plate} must appear exactly once in {batch_dir / 'barcode_platemap.csv'}"
        )
    return pd.read_csv(
        batch_dir / "platemap" / f"{match.iloc[0]}.txt", sep="\t", dtype=str
    )


def simulate_plate(
    plate: str,
    platemap: pd.DataFrame,
    well_effects: WellEffects,
    features: list[Feature],
    loadings: np.ndarray,
    seed: int,
) -> pd.DataFrame:
    """Simulate converted single-cell profiles for the wells of one plate."""
    rng = keyed_rng(seed, f"plate:{plate}")
    plate_offset = rng.normal(0, PLATE_SD, N_LATENT)
    well_latent = (
        well_effects.latent_means
        + plate_offset
        + rng.normal(0, WELL_SD, well_effects.latent_means.shape)
    )
    wells = platemap["well_position"].to_numpy()
    n_cells = np.maximum(1, rng.poisson(well_effects.mean_cells))
    well_index = np.repeat(np.arange(len(wells)), n_cells)
    n = len(well_index)

    # cells are listed by image, as from CytoTable; one image per site in each well
    site = rng.integers(1, SITES_PER_WELL + 1, n)
    image_number = well_index * SITES_PER_WELL + site
    order = np.argsort(image_number, kind="stable")
    well_index = well_index[order]
    site = site[order]
    image_number = image_number[order]

    cell_latent = well_latent[well_index] + rng.normal(0, 1, (n, N_LATENT))
    z = cell_latent @ loadings.T + rng.normal(0, FEATURE_NOISE_SD, (n, len(features)))
    feature_values = pd.DataFrame(
        transform_values(z, features), columns=[f.name for f in features]
    )

    meta = pd.DataFrame(
        {
            "Metadata_Plate": plate,
            "Metadata_Well": wells[well_index],
            "Metadata_ImageNumber": image_number,
            "Image_Metadata_Site": site,
        }
    )
    # number the objects within each image
    object_number = meta.groupby("Metadata_ImageNumber").cumcount() + 1
    meta.insert(3, "Metadata_ObjectNumber", object_number)
    for comp in COMPARTMENTS:
        meta[f"{comp}_Number_Object_Number"] = object_number
    return pd.concat([meta, feature_values], axis=1)


def write_platemaps(platemaps_dir: Path, dataset: Dataset, metadata_dir: Path) -> None:
    """Copy the dataset's platemaps to the gallery layout, with barcode_platemap.csv limited to its plates."""
    batch_dir = platemaps_dir / dataset.batch
    out_dir = metadata_dir / "platemaps" / dataset.batch
    (out_dir / "platemap").mkdir(parents=True, exist_ok=True)
    barcodes = pd.read_csv(batch_dir / "barcode_platemap.csv", dtype=str)
    barcodes = barcodes[barcodes["Assay_Plate_Barcode"].isin(dataset.plates)]
    barcodes.to_csv(out_dir / "barcode_platemap.csv", index=False)
    for platemap_name in barcodes["Plate_Map_Name"]:
        platemap_file = f"{platemap_name}.txt"
        (out_dir / "platemap" / platemap_file).write_bytes(
            (batch_dir / "platemap" / platemap_file).read_bytes()
        )


def generate_dataset(
    dataset: Dataset,
    platemaps_dir: Path,
    output_dir: Path,
    features: list[Feature],
    loadings: np.ndarray,
    seed: int,
) -> None:
    """Write the profiles and platemaps for one dataset."""
    workspace = output_dir / dataset.project / SOURCE / "workspace"
    write_platemaps(
        platemaps_dir=platemaps_dir,
        dataset=dataset,
        metadata_dir=workspace / "metadata",
    )
    for plate in dataset.plates:
        platemap = read_platemap(
            platemaps_dir=platemaps_dir, batch=dataset.batch, plate=plate
        )
        well_effects = WELL_EFFECTS[dataset.name](platemap, seed)
        profiles = simulate_plate(
            plate=plate,
            platemap=platemap,
            well_effects=well_effects,
            features=features,
            loadings=loadings,
            seed=seed,
        )
        parquet_path = (
            workspace
            / "profiles"
            / dataset.batch
            / plate
            / f"{plate}_converted.parquet"
        )
        parquet_path.parent.mkdir(parents=True, exist_ok=True)
        profiles.to_parquet(parquet_path, index=False, compression="zstd")
        print(
            f"{dataset.name} {plate}: {len(platemap)} wells, {len(profiles)} cells, {profiles.shape[1]} columns"
        )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate simulated full-size single-cell profiles for the test_full profiles."
    )
    parser.add_argument(
        "--dataset",
        choices=[d.name for d in DATASETS],
        action="append",
        help="Dataset to generate; repeat for more than one (default: all)",
    )
    parser.add_argument(
        "--platemaps-dir",
        type=Path,
        default=Path("assets/pccma/platemaps"),
        help="Directory of PCCMA platemaps, by batch (default: %(default)s)",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("tests/data-full"),
        help="Directory to write the gallery layout to (default: %(default)s)",
    )
    parser.add_argument("--seed", type=int, default=2026, help="Random seed")
    args = parser.parse_args()

    if not args.platemaps_dir.is_dir():
        raise SystemExit(
            f"Platemaps directory {args.platemaps_dir} not found; build it as described in assets/pccma/README.md"
        )
    selected = args.dataset or [d.name for d in DATASETS]
    features = build_features()
    loadings = build_loadings(features=features, seed=args.seed)
    for dataset in DATASETS:
        if dataset.name in selected:
            generate_dataset(
                dataset=dataset,
                platemaps_dir=args.platemaps_dir,
                output_dir=args.output_dir,
                features=features,
                loadings=loadings,
                seed=args.seed,
            )


if __name__ == "__main__":
    main()
