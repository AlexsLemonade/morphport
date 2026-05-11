# CLAUDE.md

You are working in a Childhood Cancer Data Lab repository.
This project is a Nextflow pipeline for processing single-cell imaging data from the Pediatric Cancer Cell Atlas (PCCMA).
The pipeline adheres to nf-core guidelines as much as possible to promote modularity and reproducibility.

## Rules

- Do NOT run `git commit`, `git push`, or any other git write commands. The human will handle all git operations.
- Do NOT create, read, or modify `.env` files or any file containing credentials, tokens, or secrets.
- Do NOT make network requests to external services (no `curl`, `wget`, API calls, etc.). However, you may use WebSearch and WebFetch tools to retrieve documentation or code examples relevant to the task at hand.
- Do NOT install system-level packages or modify system configuration.
- Do NOT access or reference any data that is not already present in this repository.

## Expectations

- When you have made changes, stop and describe what you changed and why so the human can review.
- If you are unsure whether an action is allowed, ask rather than proceeding.
- Keep changes small and incremental. Prefer multiple small steps over one large batch of changes.

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
