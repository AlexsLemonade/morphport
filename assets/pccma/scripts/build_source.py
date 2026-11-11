#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "openpyxl==3.1.5",
#     "pandas==2.3.3",
# ]
# ///
"""
Download the PCCMA source metadata files from the upstream GitHub repositories.

Files are downloaded at the commit pinned for each repository in UPSTREAM_REPOS below;
to update the source files, change the pinned commit and rerun this script.
The files matching each pattern are found from the repository's file list at that commit, from the GitHub API.
CSV and text files are copied unchanged.
Excel sheets are converted to CSV with every cell read as text, so values are not reformatted.
Each file is written to the folder given for it in COPY_FILES or EXCEL_SHEETS, keeping its upstream file name;
each Excel sheet is written to <workbook stem>_<sheet name>.csv, with spaces in sheet names replaced by underscores.
The script also writes source/README.md, which lists every file with a link to its upstream source.

Usage, from the repository root:

    uv run assets/pccma/scripts/build_source.py
"""

import argparse
import io
import json
import shutil
import urllib.request
from fnmatch import fnmatch
from pathlib import Path, PurePosixPath

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

# Files to copy unchanged: (repository, path relative to the repository root, folder in source/);
# the file name part of a path may be a pattern with wildcards, which match files in that one folder
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


def download(url: str) -> bytes:
    """Return the contents of a URL."""
    with urllib.request.urlopen(url) as response:
        return response.read()


def list_repo_files(repo_key: str) -> list[str]:
    """Return the paths of all files in a repository at its pinned commit, from the GitHub API."""
    repo = UPSTREAM_REPOS[repo_key]
    tree_url = f"https://api.github.com/repos/{repo['github']}/git/trees/{repo['commit']}?recursive=1"
    tree = json.loads(download(tree_url))
    if tree["truncated"]:
        raise SystemExit(f"The GitHub file list for {repo['github']} is incomplete")
    return [item["path"] for item in tree["tree"] if item["type"] == "blob"]


def matching_paths(repo_files: list[str], pattern: str) -> list[str]:
    """Return the repository files in the pattern's folder whose names match the pattern's file name part."""
    pattern_path = PurePosixPath(pattern)
    return sorted(
        path
        for path in repo_files
        if PurePosixPath(path).parent == pattern_path.parent
        and fnmatch(PurePosixPath(path).name, pattern_path.name)
    )


def raw_url(repo_key: str, upstream_path: str) -> str:
    """Return the URL to download a file at the pinned commit of its repository."""
    repo = UPSTREAM_REPOS[repo_key]
    return f"https://raw.githubusercontent.com/{repo['github']}/{repo['commit']}/{upstream_path}"


def upstream_url(repo_key: str, upstream_path: str) -> str:
    """Return the GitHub URL of a file at the pinned commit of its repository."""
    repo = UPSTREAM_REPOS[repo_key]
    return f"https://github.com/{repo['github']}/blob/{repo['commit']}/{upstream_path}"


def plan_file_copies(repo_files: dict[str, list[str]]) -> list[dict]:
    """List the files in COPY_FILES, with the path each will be copied to."""
    plan = []
    for repo_key, pattern, folder in COPY_FILES:
        matches = matching_paths(repo_files=repo_files[repo_key], pattern=pattern)
        if not matches:
            raise SystemExit(
                f"No files in {UPSTREAM_REPOS[repo_key]['github']} match {pattern}"
            )
        for upstream_path in matches:
            plan.append(
                {
                    "raw_url": raw_url(repo_key=repo_key, upstream_path=upstream_path),
                    "sheet_name": None,
                    "copy_path": f"{folder}/{PurePosixPath(upstream_path).name}",
                    "upstream_path": upstream_path,
                    "url": upstream_url(repo_key=repo_key, upstream_path=upstream_path),
                    "conversion": "copied unchanged",
                }
            )
    return plan


def plan_sheet_conversions() -> list[dict]:
    """List the Excel sheets in EXCEL_SHEETS, with the path each will be converted to."""
    plan = []
    for repo_key, workbook_path, sheet_names, folder in EXCEL_SHEETS:
        workbook_stem = PurePosixPath(workbook_path).stem
        for sheet_name in sheet_names:
            sheet_stem = sheet_name.replace(" ", "_")
            plan.append(
                {
                    "raw_url": raw_url(repo_key=repo_key, upstream_path=workbook_path),
                    "sheet_name": sheet_name,
                    "copy_path": f"{folder}/{workbook_stem}_{sheet_stem}.csv",
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


def write_copy(entry: dict, contents: bytes, output_dir: Path) -> None:
    """Write one downloaded file unchanged, or convert one Excel sheet to CSV, as described by a plan entry."""
    destination = output_dir / entry["copy_path"]
    destination.parent.mkdir(parents=True, exist_ok=True)
    if entry["sheet_name"] is None:
        destination.write_bytes(contents)
    else:
        sheet = pd.read_excel(
            io.BytesIO(contents),
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
        "To recreate the other files, run the script, which downloads them from each repository below at its pinned commit.",
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
        "--output-dir",
        type=Path,
        default=Path(__file__).resolve().parent.parent / "source",
        help="directory to write the source files to (default: assets/pccma/source)",
    )
    args = parser.parse_args()

    repo_files = {repo_key: list_repo_files(repo_key) for repo_key in UPSTREAM_REPOS}
    plan = plan_file_copies(repo_files=repo_files) + plan_sheet_conversions()
    check_unique_destinations(plan=plan)

    # Download every file before clearing the output directory, so a failed download leaves it intact
    downloads = {
        url: download(url) for url in sorted({entry["raw_url"] for entry in plan})
    }

    # Start from an empty output directory, so files removed upstream do not linger
    if args.output_dir.exists():
        shutil.rmtree(args.output_dir)
    args.output_dir.mkdir(parents=True)

    for entry in plan:
        write_copy(
            entry=entry,
            contents=downloads[entry["raw_url"]],
            output_dir=args.output_dir,
        )
    write_readme(manifest=plan, output_dir=args.output_dir)
    print(f"Wrote {len(plan)} files to {args.output_dir}")


if __name__ == "__main__":
    main()
