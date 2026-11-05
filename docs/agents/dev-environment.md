# Dev environment setup

How to get Nextflow, nf-test, nf-core, and conda working in the agent sandbox.
Read this only when a tool is missing or a command fails in a way that looks environmental.
If `nextflow -version`, `nf-test version`, and `nf-core --version` all work, skip it.

## Permissions

This is a pre-approved exception to the install and network rules in `CLAUDE.md`.
When I ask you to install or set up tooling, follow this file without asking for confirmation, then report what you did.
The other rules in `CLAUDE.md` still apply, in particular the rule against piping downloaded content into a shell.

Allowed tools: Nextflow, nf-test, nf-core, Java (the human installs it with `sudo`), conda via Miniforge, and a working `uv`.
Updates to these tools are also allowed with approval.

Network access is allowed for these tools' own downloads (GitHub, `registry.nextflow.io`, container registries, conda channels, PyPI) and for `nextflow run` on a pipeline I have named explicitly.
Ask before running any pipeline I have not named.

Limits:

- Install only under `~/.local`, `~/miniforge3`, `~/.nf-test`, and `~/.nxf-conda-cache`.
- No `sudo` and no system package changes; if a package is needed (for example Java), tell me the command to run.
- Never run an installer inside the repo; use the scratchpad.
- Do not use this exception for any other tool, package manager, or service.
  An explicit request from me in chat to install a specific tool also counts as authorization.
- If an install fails in a way this file does not cover, stop and ask.
  Do not try other install methods or download sources.

The rest of this file holds the procedures and the known fixes.

> [!WARNING]
> Always read a shell script before running it.
> Download installers to the scratchpad as files, never pipe them into a shell, and read the whole script before executing it.
> While reading, explicitly check for dangerous behavior: `sudo` or privilege changes, `rm`/`mv`/`cp` outside the install location, edits to shell rc files or system configuration, reads of credentials or tokens, requests to unexpected hosts, and obfuscated code (`base64`, `eval`, long escaped strings).
> State in your report that you checked, and what you found.
> If a script cannot be read in full, for example a self-extracting installer with a binary payload, read its header and options, verify its published checksum, and say so when reporting what you ran.
> Prefer running it with flags that avoid modifying shell rc files or system configuration (for example Miniforge's `-b`).

## Quick status check

```bash
nextflow -version
nf-test version
nf-core --version
java -version
conda --version
```

## Install order and known fixes

### Java (the human runs these; they need sudo)

Use the apt JDK, not SDKMAN.
SDKMAN's Temurin JDK ships its own truststore, which does not trust the sandbox proxy CA, so every Java HTTPS call (plugin registry, GitHub) fails with `PKIX path building failed`.

```bash
sudo apt-get install -y default-jdk-headless
sudo update-ca-certificates --fresh
```

If `/etc/ssl/certs/java/cacerts` is still missing afterwards, run `sudo /var/lib/dpkg/info/ca-certificates-java.postinst configure`.

### Nextflow

Download `https://get.nextflow.io` to the scratchpad (not the repo), then copy the file to `~/.local/bin/nextflow` and make it executable.
The downloaded file is the launcher itself; running it from the scratchpad only prints help.
The first `nextflow -version` downloads the runtime.

### nf-test

Download `https://get.nf-test.com` to the scratchpad and run it with `bash`, not `sh`: dash rejects the `${VERSION:1}` substitution.
Copy the extracted `nf-test` launcher to `~/.local/bin`.
The installer also places `nf-test.jar` in `~/.nf-test`.

### uv and nf-core

First run `uv --version`.
If it works, skip to `uv tool install nf-core`.

The apt `uv` package (0.13.7, DHI repo) crashes on kernels with 16 KB pages (`jemalloc: Unsupported system page size`).
If `/usr/bin/uv --version` fails this way, install the PyPI build into a throwaway venv in the scratchpad, copy `bin/uv` and `bin/uvx` to `~/.local/bin`, then run `uv tool install nf-core`.
`~/.local/bin` comes before `/usr/bin` on `PATH`, so the working build shadows the broken one.

Once the apt package is fixed or rebuilt, this workaround is unnecessary: `sudo apt-get install uv` (the human runs it) and delete `~/.local/bin/uv` and `~/.local/bin/uvx`.

### conda via Miniforge

The sandbox is arm64 and the pinned Wave container images are amd64 only, so container-based tests fail with `exec format error`.
Use `--profile +conda` for those tests.
Miniforge provides a real `conda`, which is what Nextflow and the nf-core utils subworkflow expect.
(micromamba was tried first and needed a shim for `conda info --json`, `bin/activate`, and `conda config --show channels`, so it is not used.)

1. Download the aarch64 installer and its checksum from the latest release at `https://github.com/conda-forge/miniforge/releases` into the scratchpad.
   The checksum file is named with the version, for example `Miniforge3-26.9.4-1-Linux-aarch64.sh.sha256`.
   Verify it with `sha256sum`.
   The installer is a self-extracting script with a binary payload, so read its header and options and rely on the checksum.
2. Install in batch mode, which does not modify shell rc files: `bash Miniforge3-Linux-aarch64.sh -b -p $HOME/miniforge3`.
3. Symlink only the `conda` command, so the base environment is never auto-activated: `ln -sf $HOME/miniforge3/bin/conda ~/.local/bin/conda`.
4. Create `~/.condarc` with both channels in this order, because the nf-core check requires them:

   ```yaml
   channels:
     - conda-forge
     - bioconda
   ```

5. Point Nextflow's environment cache at local disk for conda runs: `export NXF_CONDA_CACHEDIR=$HOME/.nxf-conda-cache`.
   Environments are not relocatable, so do not rename or move this directory after it is populated.

The first conda run builds one environment per module (a few minutes); later runs reuse the cache.

## Firewall hosts

Blocked requests return HTTP 403 with "Approval required for `<host>`".
The human approves them on the host with `sbx policy allow network <hosts>` or the sbx TUI.
Pre-approving this list avoids repeated round trips:

- Nextflow: `get.nextflow.io`, `registry.nextflow.io`, `github.com`
- Plugin and image blobs: `public-cr-prod.seqera.io`, `community.wave.seqera.io`, `cerbero.seqera.io`, `community-cr-prod.seqera.io`
- nf-test: `get.nf-test.com`
- conda: `conda.anaconda.org` (Miniforge installer comes from `github.com` release assets)
- uv and nf-core: `pypi.org`, `files.pythonhosted.org`

## Testing notes

- Run `uv run scripts/generate_test_data.py` first; the synthetic profiles are not tracked.
- Under conda, the `-profile test` snapshot differs only in software versions (for example `pyarrow`); do not update snapshots from conda runs.
- Run background commands so their own exit code is captured (for example `nf-test test ...; echo "EXIT=$?"`).
  A trailing `echo` makes the harness report exit 0 regardless of the test result.
- `nf-test test file.nf.test@hash` keeps only one test per file when several are given; run whole files instead.
