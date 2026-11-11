#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "openpyxl==3.1.5",
#     "pandas==2.3.3",
# ]
# ///
"""
Copy the PCCMA source metadata files from local clones of the upstream repositories.

Each upstream repository must be checked out at the commit pinned in UPSTREAM_REPOS below;
to update the source files, change the pinned commit, check it out, and rerun this script.
CSV and text files are copied unchanged.
Excel sheets are converted to CSV with every cell read as text, so values are not reformatted.
Each file is written to the folder given for it in COPY_FILES or EXCEL_SHEETS, keeping its upstream file name;
each Excel sheet is written to <workbook stem>_<sheet name>.csv, with spaces in sheet names replaced by underscores.
The script also writes source/README.md, which lists every file with a link to its upstream source.

Usage, from the repository root:

    uv run assets/pccma/scripts/build_source.py \\
        --pccma-repo path/to/pccma \\
        --repo1-repo path/to/pccma_repo1_screen_image_profiling
"""

import argparse
import shutil
import subprocess
from pathlib import Path

import pandas as pd

# Upstream repositories, keyed by a short name used below
UPSTREAM_REPOS = {
    "pccma": {
        "github": "WayScience/pccma",
        "commit": "bc7d7dfc2185bc431a2d4a6a5108d52d53ee05bd",
    },
    "repo1": {
        "github": "WayScience/pccma_repo1_screen_image_profiling",
        "commit": "298c35e444ef419d7ffb7d4ef9a8b737d9f89da6",
    },
}

# Files to copy unchanged: (repository, glob pattern relative to the repository root, folder in source/)
COPY_FILES = [
    ("pccma", "LICENSE", "pccma"),
    ("pccma", "cell_lines/pccma_cell_line_metadata.csv", "pccma"),
    ("pccma", "cell_lines/pccma_cell_line_metadata_columns.txt", "pccma"),
    ("pccma", "0.download_data/metadata/platemaps/*_platemap.csv", "pccma/platemaps"),
    ("repo1", "LICENSE", "repo1"),
    (
        "repo1",
        "repo1_screen_metadata/raw_data/repo1_moa_metadata_annotation.csv",
        "repo1",
    ),
    (
        "repo1",
        "repo1_screen_metadata/raw_data/repo1_target_metadata_annotation.csv",
        "repo1",
    ),
    ("repo1", "SK-N-AS_repo1_screen/0.download_data/metadata/*.csv", "repo1/SK-N-AS"),
]

# Excel sheets to convert to CSV: (repository, path to workbook, sheet names, folder in source/)
EXCEL_SHEETS = [
    (
        "repo1",
        "repo1_screen_metadata/REPO2025_Broad_Cpd_ID.xlsx",
        ["REPO2025", "ReadMe"],
        "repo1",
    ),
]


def check_commit(repo_dir: Path, expected_commit: str) -> None:
    """Fail unless the repository at repo_dir is checked out at expected_commit."""
    result = subprocess.run(
        ["git", "-C", str(repo_dir), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    )
    head_commit = result.stdout.strip()
    if head_commit != expected_commit:
        raise SystemExit(
            f"{repo_dir} is at commit {head_commit}, but {expected_commit} is pinned. "
            "Check out the pinned commit, or update the pin in this script."
        )


def upstream_url(repo_key: str, upstream_path: str) -> str:
    """Return the GitHub URL of a file at the pinned commit of its repository."""
    repo = UPSTREAM_REPOS[repo_key]
    return f"https://github.com/{repo['github']}/blob/{repo['commit']}/{upstream_path}"


def plan_file_copies(repo_dirs: dict[str, Path]) -> list[dict]:
    """List the files in COPY_FILES, with the path each will be copied to."""
    plan = []
    for repo_key, pattern, folder in COPY_FILES:
        repo_dir = repo_dirs[repo_key]
        matches = sorted(repo_dir.glob(pattern))
        if not matches:
            raise SystemExit(f"No files in {repo_dir} match {pattern}")
        for source_path in matches:
            upstream_path = str(source_path.relative_to(repo_dir))
            plan.append(
                {
                    "source_path": source_path,
                    "sheet_name": None,
                    "copy_path": f"{folder}/{source_path.name}",
                    "upstream_path": upstream_path,
                    "url": upstream_url(repo_key=repo_key, upstream_path=upstream_path),
                    "conversion": "copied unchanged",
                }
            )
    return plan


def plan_sheet_conversions(repo_dirs: dict[str, Path]) -> list[dict]:
    """List the Excel sheets in EXCEL_SHEETS, with the path each will be converted to."""
    plan = []
    for repo_key, workbook_path, sheet_names, folder in EXCEL_SHEETS:
        workbook = repo_dirs[repo_key] / workbook_path
        for sheet_name in sheet_names:
            sheet_stem = sheet_name.replace(" ", "_")
            plan.append(
                {
                    "source_path": workbook,
                    "sheet_name": sheet_name,
                    "copy_path": f"{folder}/{workbook.stem}_{sheet_stem}.csv",
                    "upstream_path": workbook_path,
                    "url": upstream_url(repo_key=repo_key, upstream_path=workbook_path),
                    "conversion": f"sheet `{sheet_name}` converted to CSV",
                }
            )
    return plan


def check_unique_destinations(plan: list[dict]) -> None:
    """Fail if more than one file would be written to the same path."""
    copy_paths = [entry["copy_path"] for entry in plan]
    duplicates = sorted({path for path in copy_paths if copy_paths.count(path) > 1})
    if duplicates:
        raise SystemExit(
            f"More than one source file would be written to: {', '.join(duplicates)}"
        )


def write_copy(entry: dict, output_dir: Path) -> None:
    """Copy one file unchanged, or convert one Excel sheet to CSV, as described by a plan entry."""
    destination = output_dir / entry["copy_path"]
    destination.parent.mkdir(parents=True, exist_ok=True)
    if entry["sheet_name"] is None:
        shutil.copyfile(entry["source_path"], destination)
    else:
        sheet = pd.read_excel(
            entry["source_path"],
            sheet_name=entry["sheet_name"],
            dtype=str,
            na_filter=False,
        )
        sheet.to_csv(destination, index=False)


def write_readme(manifest: list[dict], output_dir: Path) -> None:
    """Write source/README.md, describing the upstream repositories and every copied file."""
    lines = [
        "# PCCMA source metadata",
        "",
        "Faithful copies of metadata files from the upstream PCCMA repositories.",
        "Do not edit these files by hand; they are written by `assets/pccma/scripts/build_source.py`, which also writes this README.",
        "CSV and text files are copied unchanged, and Excel sheets are converted to CSV with every cell read as text.",
        "",
        "Only this README is tracked by git; the upstream license notices are in `assets/pccma/README.md`.",
        "To recreate the other files, clone each repository below, check out its pinned commit, and run the script.",
        "",
        "## Upstream repositories",
        "",
        "Both repositories are licensed under the BSD 3-Clause License; their licenses are copied to `pccma/LICENSE` and `repo1/LICENSE`, and reproduced in `assets/pccma/README.md`.",
        "",
        "| Repository | Commit |",
        "| ---------- | ------ |",
    ]
    for repo in UPSTREAM_REPOS.values():
        lines.append(
            f"| [{repo['github']}](https://github.com/{repo['github']}) | `{repo['commit']}` |"
        )
    lines.extend(
        [
            "",
            "## Files",
            "",
            "Each source link points to the file at the pinned commit.",
            "",
            "| File | Source | Conversion |",
            "| ---- | ------ | ---------- |",
        ]
    )
    for entry in sorted(manifest, key=lambda item: item["copy_path"]):
        lines.append(
            f"| `{entry['copy_path']}` | [{entry['upstream_path']}]({entry['url']}) | {entry['conversion']} |"
        )
    (output_dir / "README.md").write_text("\n".join(lines) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--pccma-repo", type=Path, required=True, help="local clone of WayScience/pccma"
    )
    parser.add_argument(
        "--repo1-repo",
        type=Path,
        required=True,
        help="local clone of WayScience/pccma_repo1_screen_image_profiling",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path(__file__).resolve().parent.parent / "source",
        help="directory to write the source files to (default: assets/pccma/source)",
    )
    args = parser.parse_args()

    repo_dirs = {"pccma": args.pccma_repo, "repo1": args.repo1_repo}
    for repo_key, repo_dir in repo_dirs.items():
        check_commit(
            repo_dir=repo_dir, expected_commit=UPSTREAM_REPOS[repo_key]["commit"]
        )

    # Start from an empty output directory, so files removed upstream do not linger
    if args.output_dir.exists():
        shutil.rmtree(args.output_dir)
    args.output_dir.mkdir(parents=True)

    plan = plan_file_copies(repo_dirs=repo_dirs) + plan_sheet_conversions(
        repo_dirs=repo_dirs
    )
    check_unique_destinations(plan=plan)
    for entry in plan:
        write_copy(entry=entry, output_dir=args.output_dir)
    write_readme(manifest=plan, output_dir=args.output_dir)
    print(f"Wrote {len(plan)} files to {args.output_dir}")


if __name__ == "__main__":
    main()
