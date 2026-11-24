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
- screen: SK-N-AS cells treated with REPO1 compounds, from two plate layouts of the SK-N-AS_REPO1_Screen batch,
  with three replicate plates of each, plus simulated U2-OS plates with the same layouts
  (the real screen has only SK-N-AS; the U2-OS plates are included to test multiple cell lines in a screen)

Well factor means are simulated first by combining dataset effects (e.g., cell line, time point, or compound), a plate offset, and well noise.
Each cell then adds its own noise to its well's mean, and a fixed matrix maps the result to features, plus feature noise.
Features from the same channel share most of their mapping, so they are correlated, as in real data.
Features that measure nearly the same thing (e.g. a cell's area and perimeter, or a channel's mean and median intensity)
form families that also share most of their noise, so they are highly correlated even between single cells.
Finally, values are transformed to a plausible range for each measurement
(e.g., positive areas, fractions from 0 to 1, correlations from -1 to 1).

Feature names follow CellProfiler conventions, with the PCCMA channel names (see CHANNELS),
so use assets/cosmicqc_filter_settings_pccma.json for coSMicQC filtering.

Random effects are drawn from generators seeded by --seed and a name (e.g. a cell line, compound, or plate barcode),
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
class DerivedPlate:
    """
    A plate that is not in the PCCMA platemaps, copied from a real plate's platemap with a different cell line.
    Its barcode is made up, and its platemap is named after it.
    """

    plate: str
    source_plate: str
    cell_line: str


@dataclass(frozen=True)
class Dataset:
    """
    A simulated project: one batch of plates.
    plates are real PCCMA plates; derived_plates are added to the batch with copies of real platemaps.
    Each dataset's well effects are set by the function for its name in WELL_EFFECTS.
    """

    name: str
    project: str
    batch: str
    plates: tuple[str, ...]
    derived_plates: tuple[DerivedPlate, ...] = ()


# Gallery source within each project, marking the data as simulated
SOURCE = "simulated"

# Mean cells per well (before dataset effects on cell counts), spread over the sites (images) in each well
CELLS_PER_WELL = 50
SITES_PER_WELL = 9

# Latent factor model: number of latent dimensions, and standard deviations of each source of variation
# in latent units (cell noise has standard deviation 1)
N_LATENT = 16
PLATE_SD = 0.1
WELL_SD = 0.1
# Feature noise, added to each feature family after mapping from the latent space (shared by the family's features)
FEATURE_NOISE_SD = 0.3
# Weight of the shared channel (or shape) loadings in each feature family's loadings, versus the family's own random loadings
GROUP_LOADING_WEIGHT = 0.6
# Features in a family (see Measurement) share all but FAMILY_OWN_WEIGHT of their loadings,
# and each adds its own noise, with standard deviation FAMILY_MEMBER_NOISE_SD, to the family's
FAMILY_OWN_WEIGHT = 0.15
FAMILY_MEMBER_NOISE_SD = 0.1

# Atlas effects, in latent units: each cell line's offset (per latent dimension),
# and the time point effect at 72 h (along a shared direction, scaled by each cell line's growth rate)
CELL_LINE_SD = 0.5
TIME_EFFECT_72H = 1.5
GROWTH_RATE_SD = 0.3

# Screen effects, in latent units: the fraction of test compounds that are active in every cell line,
# and the chance that any other compound is active in each cell line (about 10% of compounds are active in each,
# and about half of those active in one of two cell lines are active only in that one);
# the range of active compounds' effect sizes, and the standard deviation of each cell line's log scale on them;
# the weight of the shared mechanism of action direction in each active compound's effect direction;
# and the size of the untreated wells' offset from DMSO
SHARED_ACTIVE_FRACTION = 0.06
CELL_LINE_ACTIVE_FRACTION = 0.04
ACTIVE_EFFECT_RANGE = (0.5, 3.0)
CELL_LINE_RESPONSE_SD = 0.3
MECHANISM_WEIGHT = 0.5
UNTREATED_EFFECT = 0.3

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
    # two layouts, each with three replicate plates; the Row O repeat plates (BR00149048, BR00149049) are left out
    Dataset(
        name="screen",
        project="pccma-repo1-simulated",
        batch="SK-N-AS_REPO1_Screen",
        plates=(
            "BR00148919",
            "BR00148920",
            "BR00148921",
            "BR00148922",
            "BR00148923",
            "BR00148924",
        ),
        # simulated U2-OS plates with the same layouts, with made-up barcodes (SIM + the source barcode's number)
        derived_plates=tuple(
            DerivedPlate(
                plate=f"SIM{source_plate.removeprefix('BR')}",
                source_plate=source_plate,
                cell_line="U2-OS",
            )
            for source_plate in (
                "BR00148919",
                "BR00148920",
                "BR00148921",
                "BR00148922",
                "BR00148923",
                "BR00148924",
            )
        ),
    ),
]


@dataclass(frozen=True)
class Measurement:
    """
    A CellProfiler measurement, before adding the compartment prefix.
    kind sets how simulated values are transformed: positive (log-normal around center),
    fraction (between 0 and 1), correlation (between -1 and 1), or count (non-negative integer).
    family names the measurements that measure nearly the same thing (e.g. Area and Perimeter);
    within a compartment, they share most of their loadings and noise. None means the measurement is its own family.
    """

    name: str
    group: str
    kind: str
    center: float
    family: str | None = None


@dataclass(frozen=True)
class Feature:
    """A simulated feature: its full name, its family (see Measurement), and how its values are generated."""

    name: str
    group: str
    kind: str
    center: float
    family: str


@dataclass(frozen=True)
class WellEffects:
    """Well effects for one plate (latent means and mean cell counts), in platemap row order."""

    latent_means: np.ndarray  # (wells, N_LATENT)
    mean_cells: np.ndarray  # (wells,)


def channel_measurements() -> list[Measurement]:
    """Return the measurements made in each imaging channel."""
    measurements = []
    for ch in CHANNELS:
        # the typical intensity of the object: mean, median, and quartiles
        level_family = f"Intensity_Level_{ch}"
        for name, kind, center, family in [
            ("IntegratedIntensity", "positive", 50.0, None),
            ("MeanIntensity", "fraction", 0.1, level_family),
            ("StdIntensity", "fraction", 0.03, None),
            ("MedianIntensity", "fraction", 0.1, level_family),
            ("MinIntensity", "fraction", 0.02, None),
            ("MaxIntensity", "fraction", 0.4, None),
            ("LowerQuartileIntensity", "fraction", 0.06, level_family),
            ("UpperQuartileIntensity", "fraction", 0.15, level_family),
            ("MassDisplacement", "positive", 1.0, None),
        ]:
            measurements.append(
                Measurement(f"Intensity_{name}_{ch}", ch, kind, center, family)
            )
        # how uniform the texture is
        uniformity_family = f"Texture_Uniformity_{ch}"
        for name, kind, center, family in [
            ("AngularSecondMoment", "fraction", 0.2, uniformity_family),
            ("Contrast", "positive", 2.0, None),
            ("Correlation", "correlation", 0.5, None),
            ("Entropy", "positive", 5.0, uniformity_family),
            ("InverseDifferenceMoment", "fraction", 0.6, uniformity_family),
            ("SumAverage", "positive", 20.0, None),
            ("Variance", "positive", 10.0, None),
        ]:
            measurements.append(
                Measurement(f"Texture_{name}_{ch}_3_00_256", ch, kind, center, family)
            )
        for scale in range(1, 5):
            measurements.append(
                Measurement(f"Granularity_{scale}_{ch}", ch, "positive", 1.0)
            )
        for ring in range(1, 5):
            # the fraction of intensity in each ring, as is and relative to the ring's area
            ring_family = f"RadialDistribution_Frac_{ch}_{ring}of4"
            measurements.append(
                Measurement(
                    f"RadialDistribution_FracAtD_{ch}_{ring}of4",
                    ch,
                    "fraction",
                    0.25,
                    ring_family,
                )
            )
            measurements.append(
                Measurement(
                    f"RadialDistribution_MeanFrac_{ch}_{ring}of4",
                    ch,
                    "positive",
                    1.0,
                    ring_family,
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
    # the size of the object: area, perimeter, and lengths
    size_family = "AreaShape_Size"
    measurements = [
        Measurement(f"AreaShape_{name}", SHAPE_GROUP, kind, center, family)
        for name, kind, center, family in [
            ("Area", "positive", 300.0, size_family),
            ("Perimeter", "positive", 70.0, size_family),
            ("MajorAxisLength", "positive", 25.0, size_family),
            ("MinorAxisLength", "positive", 15.0, size_family),
            ("MaxFeretDiameter", "positive", 28.0, size_family),
            ("MinFeretDiameter", "positive", 14.0, size_family),
            ("EquivalentDiameter", "positive", 20.0, size_family),
            ("Compactness", "positive", 1.2, None),
            ("FormFactor", "fraction", 0.7, None),
            ("Solidity", "fraction", 0.9, None),
            ("Extent", "fraction", 0.7, None),
            ("Eccentricity", "fraction", 0.6, None),
        ]
    ]
    for n, m in [(0, 0), (2, 0), (2, 2), (4, 0), (4, 2)]:
        measurements.append(
            Measurement(f"AreaShape_Zernike_{n}_{m}", SHAPE_GROUP, "fraction", 0.1)
        )
    return measurements


def neighbor_measurements() -> list[Measurement]:
    """Return the neighbor measurements, which are not made in a channel."""
    # the distances to the closest neighbors
    distance_family = "Neighbors_Distance"
    return [
        Measurement("Neighbors_NumberOfNeighbors_Adjacent", SHAPE_GROUP, "count", 3.0),
        Measurement(
            "Neighbors_FirstClosestDistance_Adjacent",
            SHAPE_GROUP,
            "positive",
            20.0,
            distance_family,
        ),
        Measurement(
            "Neighbors_SecondClosestDistance_Adjacent",
            SHAPE_GROUP,
            "positive",
            30.0,
            distance_family,
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
            name = f"{comp}_{m.name}"
            # families are within a compartment; a measurement without a family is its own
            family = name if m.family is None else f"{comp}_{m.family}"
            features.append(Feature(name, m.group, m.kind, m.center, family))
    return features


def family_index(features: list[Feature]) -> np.ndarray:
    """Return each feature's index in the sorted list of feature families."""
    families = sorted({f.family for f in features})
    index_of_family = {family: i for i, family in enumerate(families)}
    return np.array([index_of_family[f.family] for f in features])


def keyed_rng(seed: int, key: str) -> np.random.Generator:
    """Return a random generator for one named effect, independent of the order effects are drawn in."""
    return np.random.default_rng([seed, zlib.crc32(key.encode())])


def build_loadings(features: list[Feature], seed: int) -> np.ndarray:
    """
    Return the (features, N_LATENT) loading matrix.
    Each feature family's loadings combine its group's shared loadings with the family's own,
    and each feature's loadings combine its family's loadings with its own.
    Rows are scaled to unit length, so latent values contribute unit variance per cell.
    """
    rng = keyed_rng(seed, "loadings")
    groups = sorted({f.group for f in features})
    group_loadings = {g: rng.normal(0, 1, N_LATENT) for g in groups}
    family_groups = {f.family: f.group for f in features}
    family_loadings = {}
    for family in sorted(family_groups):
        loadings = GROUP_LOADING_WEIGHT * group_loadings[family_groups[family]] + (
            1 - GROUP_LOADING_WEIGHT
        ) * rng.normal(0, 1, N_LATENT)
        family_loadings[family] = loadings / np.linalg.norm(loadings)
    shared = np.array([family_loadings[f.family] for f in features])
    own = rng.normal(0, 1, (len(features), N_LATENT))
    own = own / np.linalg.norm(own, axis=1, keepdims=True)
    loadings = (1 - FAMILY_OWN_WEIGHT) * shared + FAMILY_OWN_WEIGHT * own
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


def unit_vector(rng: np.random.Generator) -> np.ndarray:
    """Return a random direction in the latent space."""
    direction = rng.normal(0, 1, N_LATENT)
    return direction / np.linalg.norm(direction)


def cell_line_effects(cell_line: str, seed: int) -> tuple[np.ndarray, float]:
    """Return a cell line's latent offset and growth rate, the same in every dataset."""
    cell_line_rng = keyed_rng(seed, f"cell_line:{cell_line}")
    offset = cell_line_rng.normal(0, CELL_LINE_SD, N_LATENT)
    growth_rate = np.exp(cell_line_rng.normal(0, GROWTH_RATE_SD))
    return offset, growth_rate


def compound_effect(
    compound: str, mechanisms: str, cell_line: str, seed: int
) -> np.ndarray:
    """
    Return a test compound's latent effect in a cell line, the same wherever the compound is used in that cell line.
    SHARED_ACTIVE_FRACTION of compounds are active in every cell line;
    any other compound is active in each cell line with chance CELL_LINE_ACTIVE_FRACTION, and otherwise has no effect.
    An active compound's effect size is drawn from ACTIVE_EFFECT_RANGE, then scaled for each cell line
    (log scale with standard deviation CELL_LINE_RESPONSE_SD).
    Its direction, the same in every cell line, combines a direction shared by its mechanisms of action
    (MECHANISM_WEIGHT; mechanisms are separated by "|", and none means no shared direction) with its own.
    """
    compound_rng = keyed_rng(seed, f"compound:{compound}")
    is_active_everywhere = compound_rng.random() < SHARED_ACTIVE_FRACTION
    effect_size = compound_rng.uniform(*ACTIVE_EFFECT_RANGE)
    own_direction = unit_vector(compound_rng)
    response_rng = keyed_rng(seed, f"compound:{compound}:cell_line:{cell_line}")
    is_active_in_cell_line = response_rng.random() < CELL_LINE_ACTIVE_FRACTION
    response_scale = np.exp(response_rng.normal(0, CELL_LINE_RESPONSE_SD))
    if not (is_active_everywhere or is_active_in_cell_line):
        return np.zeros(N_LATENT)
    mechanism_list = [m for m in mechanisms.split("|") if m]
    if not mechanism_list:
        direction = own_direction
    else:
        mechanism_direction = np.zeros(N_LATENT)
        for mechanism in mechanism_list:
            mechanism_direction = mechanism_direction + unit_vector(
                keyed_rng(seed, f"mechanism:{mechanism}")
            )
        mechanism_direction = mechanism_direction / np.linalg.norm(mechanism_direction)
        direction = (
            MECHANISM_WEIGHT * mechanism_direction
            + (1 - MECHANISM_WEIGHT) * own_direction
        )
    return effect_size * response_scale * direction / np.linalg.norm(direction)


def atlas_well_effects(platemap: pd.DataFrame, seed: int) -> WellEffects:
    """
    Set atlas well effects: each cell line has its own latent offset, and the time point effect grows linearly
    from none at 24 h to TIME_EFFECT_72H at 72 h along a shared direction, scaled by each cell line's growth rate.
    Cell counts grow with time, from 0.75 to 1.25 times CELLS_PER_WELL.
    """
    time_direction = unit_vector(keyed_rng(seed, "atlas:time"))
    latent_means = np.zeros((len(platemap), N_LATENT))
    mean_cells = np.zeros(len(platemap))
    for i, (cell_line, time_point) in enumerate(
        zip(platemap["Cell_Line_Name"], platemap["time_point"].astype(int))
    ):
        cell_line_offset, growth_rate = cell_line_effects(cell_line, seed)
        time_fraction = (time_point - 24) / 48
        latent_means[i] = (
            cell_line_offset
            + TIME_EFFECT_72H * growth_rate * time_fraction * time_direction
        )
        mean_cells[i] = CELLS_PER_WELL * (0.75 + 0.5 * time_fraction)
    return WellEffects(latent_means=latent_means, mean_cells=mean_cells)


def screen_well_effects(platemap: pd.DataFrame, seed: int) -> WellEffects:
    """
    Set screen well effects: each well has its cell line's offset (without a time point effect);
    test compounds add their effects in the cell line (see compound_effect), DMSO wells add none,
    and untreated wells add a small offset (UNTREATED_EFFECT) along a fixed direction.
    Cell counts do not vary with treatment, averaging CELLS_PER_WELL.
    """
    untreated_effect = UNTREATED_EFFECT * unit_vector(
        keyed_rng(seed, "screen:untreated")
    )
    latent_means = np.zeros((len(platemap), N_LATENT))
    for i, (cell_line, control_class, compound, mechanisms) in enumerate(
        zip(
            platemap["Cell_Line_Name"],
            platemap["Treatment_Control_Class"],
            platemap["compound_id"],
            platemap["Treatment_Mechanism"].fillna(""),
        )
    ):
        cell_line_offset, _growth_rate = cell_line_effects(cell_line, seed)
        if control_class == "Treatment":
            treatment_effect = compound_effect(
                compound=compound,
                mechanisms=mechanisms,
                cell_line=cell_line,
                seed=seed,
            )
        elif control_class == "NegCon":
            treatment_effect = np.zeros(N_LATENT)
        elif control_class == "Control":
            treatment_effect = untreated_effect
        else:
            raise ValueError(
                f"Unknown Treatment_Control_Class for well {platemap['well_position'].iloc[i]}: {control_class}"
            )
        latent_means[i] = cell_line_offset + treatment_effect
    mean_cells = np.full(len(platemap), float(CELLS_PER_WELL))
    return WellEffects(latent_means=latent_means, mean_cells=mean_cells)


# Well effects function for each dataset in DATASETS, by dataset name.
# Each takes a plate's platemap and --seed and returns the WellEffects (latent means and cell counts) for its wells.
WELL_EFFECTS: dict[str, Callable[[pd.DataFrame, int], WellEffects]] = {
    "atlas": atlas_well_effects,
    "screen": screen_well_effects,
}


def read_platemap(
    platemaps_dir: Path, batch: str, plate: str, keep_default_na: bool = True
) -> pd.DataFrame:
    """
    Read a plate's PCCMA platemap, found through the batch's barcode_platemap.csv.
    With keep_default_na=False, values such as "None" and empty strings are kept as strings rather than read as missing.
    """
    batch_dir = platemaps_dir / batch
    barcodes = pd.read_csv(batch_dir / "barcode_platemap.csv", dtype=str)
    match = barcodes.loc[barcodes["Assay_Plate_Barcode"] == plate, "Plate_Map_Name"]
    if len(match) != 1:
        raise ValueError(
            f"Plate {plate} must appear exactly once in {batch_dir / 'barcode_platemap.csv'}"
        )
    return pd.read_csv(
        batch_dir / "platemap" / f"{match.iloc[0]}.txt",
        sep="\t",
        dtype=str,
        keep_default_na=keep_default_na,
    )


def derive_platemap(
    platemaps_dir: Path,
    batch: str,
    derived: DerivedPlate,
    keep_default_na: bool = True,
) -> pd.DataFrame:
    """Return a derived plate's platemap: its source plate's platemap, named for the derived plate, with its cell line."""
    platemap = read_platemap(
        platemaps_dir=platemaps_dir,
        batch=batch,
        plate=derived.source_plate,
        keep_default_na=keep_default_na,
    )
    platemap["plate_map_name"] = derived.plate
    platemap["Cell_Line_Name"] = derived.cell_line
    return platemap


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
    # feature noise is shared within each family, plus a little for each feature
    families = family_index(features)
    family_noise = rng.normal(0, FEATURE_NOISE_SD, (n, families.max() + 1))
    z = (
        cell_latent @ loadings.T
        + family_noise[:, families]
        + rng.normal(0, FAMILY_MEMBER_NOISE_SD, (n, len(features)))
    )
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
    """
    Copy the dataset's platemaps to the gallery layout, with barcode_platemap.csv limited to its plates,
    and add the derived plates and their platemaps.
    """
    batch_dir = platemaps_dir / dataset.batch
    out_dir = metadata_dir / "platemaps" / dataset.batch
    (out_dir / "platemap").mkdir(parents=True, exist_ok=True)
    barcodes = pd.read_csv(batch_dir / "barcode_platemap.csv", dtype=str)
    barcodes = barcodes[barcodes["Assay_Plate_Barcode"].isin(dataset.plates)]
    for platemap_name in barcodes["Plate_Map_Name"]:
        platemap_file = f"{platemap_name}.txt"
        (out_dir / "platemap" / platemap_file).write_bytes(
            (batch_dir / "platemap" / platemap_file).read_bytes()
        )
    derived_barcodes = pd.DataFrame(
        {
            "Assay_Plate_Barcode": [d.plate for d in dataset.derived_plates],
            "Plate_Map_Name": [d.plate for d in dataset.derived_plates],
        }
    )
    for derived in dataset.derived_plates:
        platemap = derive_platemap(
            platemaps_dir=platemaps_dir,
            batch=dataset.batch,
            derived=derived,
            keep_default_na=False,
        )
        platemap.to_csv(
            out_dir / "platemap" / f"{derived.plate}.txt", sep="\t", index=False
        )
    barcodes = pd.concat([barcodes, derived_barcodes], ignore_index=True)
    barcodes.to_csv(out_dir / "barcode_platemap.csv", index=False)


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
    platemaps = {
        plate: read_platemap(
            platemaps_dir=platemaps_dir, batch=dataset.batch, plate=plate
        )
        for plate in dataset.plates
    }
    for derived in dataset.derived_plates:
        platemaps[derived.plate] = derive_platemap(
            platemaps_dir=platemaps_dir, batch=dataset.batch, derived=derived
        )
    for plate, platemap in platemaps.items():
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
