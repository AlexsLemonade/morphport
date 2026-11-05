# CLAUDE.md

You are working in a Childhood Cancer Data Lab repository.
This project is a Nextflow pipeline for processing single-cell imaging data from the Pediatric Cancer Cell Atlas (PCCMA).
The pipeline adheres to nf-core guidelines as much as possible to promote modularity and reproducibility.

## Rules

- Do NOT run `git commit`, `git push`, or any other git write commands. The human will handle all git operations. You may run `git` read commands, as well as read commands using `gh` to read issues, pull requests, and comments on GitHub.
- Do NOT create, read, or modify `.env` files or any file containing credentials, tokens, or secrets.
- Do NOT make network requests to external services without explicit authorization (no `curl`, `wget`, API calls, etc.). However, you may use WebSearch and WebFetch tools to retrieve documentation or code examples relevant to the task at hand.
- Do NOT install system-level packages or modify system configuration.
- Do NOT pipe downloaded content into a shell (no `curl ... | bash`, `wget -O- ... | sh`, or process substitution equivalents).
  Download scripts to the scratchpad as files, read the whole script, and only then run it.
  When reading, explicitly check for dangerous behavior: `sudo` or privilege changes, deleting or overwriting files outside the intended install location, edits to shell rc files or system configuration, reads of credentials or tokens, requests to unexpected hosts, and obfuscated or encoded code (`base64`, `eval`, long escaped strings).
  State in your report that you checked for these, and what you found.
  If a script cannot be read in full (for example a self-extracting installer with a binary payload), read its header and options, verify its published checksum, and say so.
- Do NOT run `nextflow run` on a pipeline the human has not named explicitly; ask first.
- Do NOT access or reference any data that is not already present in this repository.

## Expectations

- When you have made changes, stop and describe what you changed and why so the human can review.
- If you are unsure whether an action is allowed, ask rather than proceeding.
- Keep changes small and incremental. Prefer multiple small steps over one large batch of changes.

## Testing

- After finishing a set of edits, stop and describe the changes; do NOT run the full nf-test suite until the human says to.
  Quick, targeted checks (e.g. running a template helper on a small synthetic DataFrame) are fine once testing is approved.
- Pipeline tests use nf-test, with tests in `tests/` (e.g. `tests/default.nf.test` and its snapshot).
- Module tests live in `modules/local/<module>/tests/main.nf.test`; run a single module's tests with e.g. `nf-test test modules/local/<module>/tests/main.nf.test`.
- Check parquet outputs property by property (row count, column names, schema, specific values) with `path(file).parquet` (the nft-parquet plugin); never snapshot parquet md5s or calculated values.
  See `tests/README.md` for usage.
- The synthetic test profiles are not tracked by git; generate them first with `uv run scripts/generate_test_data.py` (see `tests/data/README.md`).
- Run the tests with both the default Nextflow version and the version pinned in CI (`.github/workflows/nf-test.yml`, currently 25.10.4):

  ```bash
  nf-test test
  NXF_VER=25.10.4 nf-test test
  ```

- `nf-test.config` only applies the `test` profile, so tests that run real processes (e.g. the `-profile test` pipeline test) need a software environment.
  Use conda for local testing by adding `--profile +conda` (with conda on `PATH`), e.g. `nf-test test tests/default.nf.test --profile +conda`.
  Keep the conda cache (`NXF_CONDA_CACHEDIR`) on local disk rather than a host-mounted path.
- Conda resolves newer package builds than the pinned containers, so with conda the pipeline snapshot will differ in software versions.
  Check that the run succeeds and only versions differ; do NOT update snapshots from conda runs.
  The human updates snapshots by running the full tests with Docker.
- If config parsing fails because the remote nf-core institutional config cannot be fetched, prefix the commands with `NXF_OFFLINE=true`.
- If outputs change intentionally, update snapshots with `nf-test test --update-snapshot` and point out the snapshot changes for review.
- For quick Python checks of template logic, use a throwaway uv environment rather than installing packages, e.g. `uv run --no-project --with pandas --with pyarrow python script.py`.

## Formatting guidelines

### Markdown and text files

- Use `#` for top-level headings, `##` for second-level, and so on. Do not skip heading levels.
- Use one sentence per line for regular text to improve readability in diffs and version control.
- Use fenced code blocks with language identifiers for code snippets (e.g., `python`, `bash`).
- Use bullet points or numbered lists for clarity when listing items or steps.

### Nextflow

- Use Nextflow DSL2 and adhere to strict syntax
- Follow nf-core style guidelines for Nextflow scripts, including consistent indentation (4 spaces), spacing around operators, and clear naming conventions for processes and channels.
- Include comments to explain the purpose of each process and major steps in the workflow.
- Use `script` blocks for process commands and keep them concise and focused on a single task
  - These may include groovy code for defining output files, parameters, updating `meta` objects, or other logic related to the process execution. Note that these variables should generally not use `def` since they should be available throughout the process and template and not just scoped to the script block.
  - Use `template` to reference external script files for complex commands, with a clear section where all Nextflow variables are defined and passed to the script.
- Always include `stub` blocks for creating placeholder outputs during testing or debugging.
- Do not use implicit `it` variables in closures; always define explicit input variables.
  - if an input variable is not used, prefix it with `_` to indicate it is unused (e.g., `ch_input.map { _unused, value -> ... }`).

## Agent skills

### Issue tracker

Agent-skill issues and specs are tracked as local markdown files under `.scratch/<feature>/` (gitignored), for local, individual use only.
Shared issues still belong on GitHub and should be filed as needed, but a human must file them manually; agents may draft them but must not create them.
See `docs/agents/issue-tracker.md`.

### Triage labels

The default five triage roles (`needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix`), recorded as `Status:` lines in issue files.
See `docs/agents/triage-labels.md`.

### Domain docs

Single-context: one glossary at `docs/GLOSSARY.md`, with ADRs in `planning/decisions/` named `NNN-<slug>.md` and referred to as `Decision-NNN`.
See `docs/agents/domain.md`.

### Dev environment

Installing and running Nextflow, nf-test, nf-core, conda, and Java in the agent sandbox is pre-approved within the limits in `docs/agents/dev-environment.md`.
Read it before installing anything, or when a tool fails in an environmental way (missing tool, firewall block, certificate error, architecture mismatch).
