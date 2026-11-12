#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "pandas==2.3.3",
# ]
# ///
"""
Build the PCCMA platemaps in the Cell Painting Gallery layout, from the source platemaps.

Each batch folder in platemaps/ holds barcode_platemap.csv, which maps each plate barcode to its platemap,
and platemap/<plate_map_name>.txt, a tab-separated table with one row per well.
Every plate has its own platemap, named by its barcode, as upstream.
The batches are:

- Atlas: the cell line atlas plates, with no treatments; only the seeded wells are listed, as upstream.
- SK-N-AS_REPO1_Screen: the SK-N-AS REPO1 screen plates, including the two Row O repeat plates.

Columns use the gallery's harmonized names where one applies (e.g. Cell_Line_Name, Treatment_Mechanism);
other upstream columns keep lower-case names.
In the screen platemaps, each well is a compound, DMSO, or untreated (no transfer and no solvent), recorded in
compound_id as the 13-character Broad compound ID, `DMSO`, or `untreated`.
Treatment_Concentration is blank, because the well volume, needed to find the concentration in the well, is unknown;
stock_concentration_uM and transfer_volume_nL give the source plate concentration and the transferred volume.

Usage, from the repository root:

    uv run assets/pccma/scripts/build_platemaps.py
"""

import argparse
import shutil
from pathlib import Path

import pandas as pd

PCCMA_DIR = Path(__file__).resolve().parent.parent
SOURCE_DIR = PCCMA_DIR / "source"

ATLAS_BATCH = "Atlas"
SCREEN_BATCH = "SK-N-AS_REPO1_Screen"
SCREEN_CELL_LINE = "SK-N-AS"

# Length of a Broad compound ID (e.g. BRD-K75699339), the prefix of a batch ID (e.g. BRD-K75699339-001-12-9)
COMPOUND_ID_LENGTH = 13

# Atlas source columns, renamed for the output, in output order after plate_map_name
ATLAS_COLUMNS = {
    "well_position": "well_position",
    "cell_line": "Cell_Line_Name",
    "seeding_density": "Cell_Seeding_Density",
    "time_point": "time_point",
    "plate_coating": "plate_coating",
    "pfa_fixation": "pfa_fixation",
}

# Screen output columns, in order
SCREEN_COLUMNS = [
    "plate_map_name",
    "well_position",
    "Cell_Line_Name",
    "Treatment_Broad_Sample",
    "Treatment_Primary_Treatment",
    "Treatment_Solvent",
    "Treatment_Category",
    "Treatment_Control_Class",
    "Treatment_Mechanism",
    "Treatment_Concentration",
    "compound_id",
    "stock_concentration_uM",
    "transfer_volume_nL",
]

# Values for wells without a compound, by compound_id
CONTROL_WELLS = {
    "DMSO": {"Treatment_Category": "Compound", "Treatment_Control_Class": "NegCon"},
    "untreated": {"Treatment_Category": "None", "Treatment_Control_Class": "Control"},
}


def read_text_csv(path: Path) -> pd.DataFrame:
    """Read a CSV file with every value as text and empty cells as empty strings."""
    return pd.read_csv(path, dtype=str, keep_default_na=False)


def build_atlas_platemap(
    source_platemap: pd.DataFrame, known_cell_lines: set[str]
) -> pd.DataFrame:
    """Return one atlas plate's platemap, failing on cell lines missing from the cell line table."""
    unknown = sorted(set(source_platemap["cell_line"]) - known_cell_lines)
    if unknown:
        raise SystemExit(
            f"Atlas cell lines not in cell_lines.csv: {', '.join(unknown)}"
        )
    barcodes = sorted(set(source_platemap["plate_barcode"]))
    if len(barcodes) != 1:
        raise SystemExit(f"Atlas platemap has more than one barcode: {barcodes}")
    platemap = source_platemap[list(ATLAS_COLUMNS)].rename(columns=ATLAS_COLUMNS)
    platemap.insert(0, "plate_map_name", barcodes[0])
    return platemap


def screen_well_kind(batch_id: str, solvent: str, volume_nl: str) -> str:
    """Return a screen well's compound_id: its compound ID, `DMSO`, or `untreated`, failing on any other well."""
    if batch_id != "" and solvent == "DMSO" and volume_nl != "0":
        return batch_id[:COMPOUND_ID_LENGTH]
    if batch_id == "" and solvent == "DMSO" and volume_nl != "0":
        return "DMSO"
    if batch_id == "" and solvent == "" and volume_nl == "0":
        return "untreated"
    raise SystemExit(
        f"Unexpected screen well: Batch_Id '{batch_id}', Solvent '{solvent}', Volume_nL '{volume_nl}'"
    )


def build_screen_platemap(
    source_platemap: pd.DataFrame, compounds: pd.DataFrame
) -> pd.DataFrame:
    """Return one screen plate's platemap, with names and MOAs from the compound table."""
    barcodes = sorted(set(source_platemap["Plate_Barcode"]))
    if len(barcodes) != 1:
        raise SystemExit(f"Screen platemap has more than one barcode: {barcodes}")
    compound_ids = [
        screen_well_kind(batch_id=batch_id, solvent=solvent, volume_nl=volume_nl)
        for batch_id, solvent, volume_nl in zip(
            source_platemap["Batch_Id"],
            source_platemap["Solvent"],
            source_platemap["Volume_nL"],
        )
    ]
    missing = sorted(
        set(compound_ids) - set(CONTROL_WELLS) - set(compounds["BROAD_CPD_ID"])
    )
    if missing:
        raise SystemExit(
            f"Compounds on {barcodes[0]} not in repo1_compounds.csv: {', '.join(missing)}"
        )

    names = dict(zip(compounds["BROAD_CPD_ID"], compounds["Name"]))
    moas = dict(zip(compounds["BROAD_CPD_ID"], compounds["MOA"]))
    platemap = pd.DataFrame(
        {
            "plate_map_name": barcodes[0],
            "well_position": list(source_platemap["Well_Position"]),
            "Cell_Line_Name": SCREEN_CELL_LINE,
            "Treatment_Broad_Sample": list(source_platemap["Batch_Id"]),
            "Treatment_Primary_Treatment": [
                names.get(compound_id, compound_id) for compound_id in compound_ids
            ],
            "Treatment_Solvent": list(source_platemap["Solvent"]),
            "Treatment_Category": [
                CONTROL_WELLS.get(compound_id, {}).get("Treatment_Category", "Compound")
                for compound_id in compound_ids
            ],
            "Treatment_Control_Class": [
                CONTROL_WELLS.get(compound_id, {}).get(
                    "Treatment_Control_Class", "Treatment"
                )
                for compound_id in compound_ids
            ],
            "Treatment_Mechanism": [
                moas.get(compound_id, "") for compound_id in compound_ids
            ],
            "Treatment_Concentration": "",
            "compound_id": compound_ids,
            "stock_concentration_uM": list(source_platemap["Concentration_uM"]),
            "transfer_volume_nL": list(source_platemap["Volume_nL"]),
        }
    )
    return platemap[SCREEN_COLUMNS]


def write_batch(platemaps: list[pd.DataFrame], batch_dir: Path) -> None:
    """Write a batch folder: barcode_platemap.csv and one platemap/<plate_map_name>.txt per plate."""
    (batch_dir / "platemap").mkdir(parents=True)
    plate_map_names = [platemap["plate_map_name"].iloc[0] for platemap in platemaps]
    if len(set(plate_map_names)) != len(plate_map_names):
        raise SystemExit(f"Repeated plate map names in {batch_dir}")
    for plate_map_name, platemap in zip(plate_map_names, platemaps):
        if platemap["well_position"].duplicated().any():
            raise SystemExit(f"Repeated wells in platemap {plate_map_name}")
        platemap.to_csv(
            batch_dir / "platemap" / f"{plate_map_name}.txt", sep="\t", index=False
        )
    barcode_platemap = pd.DataFrame(
        {"Assay_Plate_Barcode": plate_map_names, "Plate_Map_Name": plate_map_names}
    )
    barcode_platemap.to_csv(batch_dir / "barcode_platemap.csv", index=False)


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--atlas-dir",
        type=Path,
        default=SOURCE_DIR / "pccma" / "platemaps",
        help="directory of source atlas platemaps",
    )
    parser.add_argument(
        "--screen-dir",
        type=Path,
        default=SOURCE_DIR / "repo1" / "SK-N-AS",
        help="directory of source screen platemaps, with barcode_platemap.csv",
    )
    parser.add_argument(
        "--cell-lines",
        type=Path,
        default=PCCMA_DIR / "cell_lines.csv",
        help="cell line table",
    )
    parser.add_argument(
        "--compounds",
        type=Path,
        default=PCCMA_DIR / "repo1_compounds.csv",
        help="compound table",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=PCCMA_DIR / "platemaps",
        help="directory to write the batch folders to",
    )
    args = parser.parse_args()

    known_cell_lines = set(read_text_csv(args.cell_lines)["cell_line"])
    atlas_platemaps = [
        build_atlas_platemap(
            source_platemap=read_text_csv(path), known_cell_lines=known_cell_lines
        )
        for path in sorted(args.atlas_dir.glob("*_platemap.csv"))
    ]

    compounds = read_text_csv(args.compounds)
    screen_files = sorted(
        set(read_text_csv(args.screen_dir / "barcode_platemap.csv")["File_Name"])
    )
    screen_platemaps = [
        build_screen_platemap(
            source_platemap=read_text_csv(args.screen_dir / file_name),
            compounds=compounds,
        )
        for file_name in screen_files
    ]

    # Start from an empty output directory, so plates removed upstream do not linger
    if args.output_dir.exists():
        shutil.rmtree(args.output_dir)
    write_batch(platemaps=atlas_platemaps, batch_dir=args.output_dir / ATLAS_BATCH)
    write_batch(platemaps=screen_platemaps, batch_dir=args.output_dir / SCREEN_BATCH)
    print(
        f"Wrote {len(atlas_platemaps)} atlas and {len(screen_platemaps)} screen platemaps to {args.output_dir}"
    )


if __name__ == "__main__":
    main()
