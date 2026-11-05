# Dev environment setup

How to get Nextflow, nf-test, nf-core, and (if needed) conda working, and how to diagnose the problems that come up.
This applies to any environment: a local machine, a container, a CI runner, or a network-restricted sandbox.
Many of the known problems below depend on the environment (architecture, proxy, firewall, kernel); each entry says how to tell whether it applies.

Read this only when a tool is missing or a command fails in a way that looks environmental.
If the status check below passes, skip it.

## Permissions

This is a pre-approved exception to the install and network rules in `CLAUDE.md`.
When I ask you to install or set up tooling, follow this file without asking for confirmation, then report what you did.
The other rules in `CLAUDE.md` still apply, in particular the rule against piping downloaded content into a shell.

Allowed tools: Nextflow, nf-test, nf-core, a JDK (see below), conda via Miniforge, and `uv`.
Updates to these tools are also allowed with approval.

Network access is allowed for these tools' own downloads (GitHub, `registry.nextflow.io`, container registries, conda channels, PyPI) and for `nextflow run` on a pipeline I have named explicitly.
Ask before running any pipeline I have not named.

Limits:

- Install only under `~/.local`, `~/miniforge3`, `~/.nf-test`, and `~/.nxf-conda-cache`.
- No `sudo` and no system package changes; if a system package is needed (for example a JDK), tell me the command to run.
- Never run an installer inside the repo; use a scratch directory outside it.
- Do not use this exception for any other tool, package manager, or service.
  An explicit request from me in chat to install a specific tool also counts as authorization.
- If an install fails in a way this file does not cover, stop and ask.
  Do not try other install methods or download sources.

> [!WARNING]
> Always read a shell script before running it.
> Download installers to a scratch directory as files, never pipe them into a shell, and read the whole script before executing it.
> While reading, explicitly check for dangerous behavior: `sudo` or privilege changes, `rm`/`mv`/`cp` outside the install location, edits to shell rc files or system configuration, reads of credentials or tokens, requests to unexpected hosts, and obfuscated code (`base64`, `eval`, long escaped strings).
> State in your report that you checked, and what you found.
> If a script cannot be read in full, for example a self-extracting installer with a binary payload, read its header and options, verify its published checksum, and say so when reporting what you ran.
> Prefer running it with flags that avoid modifying shell rc files or system configuration (for example Miniforge's `-b`).

## Status check

```bash
nextflow -version
nf-test version
nf-core --version
java -version
docker info          # or conda --version, whichever software environment you use
```

Nextflow needs Java 17 or later (the Nextflow version in use sets the exact requirement).
Compare the Nextflow version with the one pinned in CI (`.github/workflows/nf-test.yml`); tests are run with both.

## Installing

Install only what is missing.
Download each installer to a scratch directory, read it, and run it from there.

- **Java**: install a JDK 17+ from the system package manager if possible (the human runs the `sudo` command).
  Prefer a distribution JDK that uses the system certificate store over a bundled-truststore build; see the certificate entry below.
- **Nextflow**: download `https://get.nextflow.io`, copy the file to `~/.local/bin/nextflow`, and make it executable.
  The downloaded file is the launcher itself, so running it from the scratch directory only prints help.
  The first `nextflow -version` downloads the runtime.
- **nf-test**: download `https://get.nf-test.com` and run it with `bash`, not `sh` (see the "Bad substitution" entry below).
  Copy the extracted `nf-test` launcher to `~/.local/bin`; the installer also places `nf-test.jar` in `~/.nf-test`.
- **nf-core**: `uv tool install nf-core` (needs a working `uv`; see the `uv` entry below if it crashes).
- **conda**: only needed when the pinned container images cannot be used (see the architecture entry below), or when the software environment is conda.
  See "Installing conda via Miniforge".

## Known problems

Match the symptom, then check whether the cause applies to this environment.

### `PKIX path building failed` or `unable to find valid certification path` from Java

Cause: the JDK's truststore does not contain the CA of a TLS-intercepting proxy (a corporate proxy or a sandbox proxy).
Other tools (`curl`, `git`) use the system store and work, while Java HTTPS calls such as plugin downloads fail.
It does not apply if there is no intercepting proxy.

Fix: use a distribution JDK that syncs the system certificate store, and refresh it.
On Debian or Ubuntu:

```bash
sudo apt-get install -y default-jdk-headless
sudo update-ca-certificates --fresh
```

If `/etc/ssl/certs/java/cacerts` is still missing, run `sudo /var/lib/dpkg/info/ca-certificates-java.postinst configure`.
Avoid JDK builds that ship their own truststore (for example one installed with SDKMAN) unless the proxy CA is imported into it.
If `JAVA_HOME` points at a JDK that was removed, run `unset JAVA_HOME`.

### `Plugin with id <plugin> not found in any repository`

Cause: Nextflow could not reach `registry.nextflow.io` or the plugin blob host.
Check `.nextflow.log` (or the nf-test `meta/nextflow.log`) for the real error: a certificate error (see above), a firewall block (see below), or a missing network connection.

### `jemalloc: Unsupported system page size` when running `uv`

Cause: the `uv` build was compiled for 4 KB memory pages and the kernel uses a different size (16 KB on some arm64 systems).
Check with `getconf PAGESIZE`; it does not apply on a 4 KB-page system.

Fix: install the upstream build instead of the distribution package.
Create a throwaway venv in the scratch directory, `pip install uv`, and copy `bin/uv` and `bin/uvx` to `~/.local/bin` so it comes before the system one on `PATH`.
When the distribution package is fixed, delete those two files.

### `exec format error` or a platform-mismatch warning from Docker

Cause: the container image architecture does not match the host (for example `linux/amd64` images on an arm64 host without emulation).
Check with `uname -m` and `docker image inspect <image> --format '{{.Architecture}}'`; it does not apply when they match.

Fix, in order of preference:

1. Run the tests on a matching host or in CI.
2. Use `--profile +conda`, which builds native environments (see below).
3. Enable emulation (QEMU/binfmt, or Rosetta on macOS); this is slower and usually needs system changes, so ask first.

### HTTP 403 "Approval required for `<host>`" (or similar blocks)

Cause: a network-restricted environment filters outbound traffic by host.
It does not apply on an open network.
Read the response body for the host and the way to approve it, and ask the human to approve it.
For the Docker and Nextflow tooling in this repo the hosts are typically:

- Nextflow: `get.nextflow.io`, `registry.nextflow.io`, `github.com`
- Plugin and image blobs: `public-cr-prod.seqera.io`, `community.wave.seqera.io`, `cerbero.seqera.io`, `community-cr-prod.seqera.io`
- nf-test: `get.nf-test.com`
- conda: `conda.anaconda.org` (the Miniforge installer comes from `github.com` release assets)
- uv and nf-core: `pypi.org`, `files.pythonhosted.org`

A download that was blocked can leave a small error file behind, and tools that resume downloads then append real data to it and fail the integrity check.
Delete the partial file and retry.

### `Bad substitution` from the nf-test installer

Cause: the installer uses `${VERSION:1}`, which `dash` (`/bin/sh` on Debian and Ubuntu) rejects.
Run it with `bash`.

### conda environment build hangs at 0% CPU with an empty cache

Cause: the conda-compatible tool is waiting for a confirmation prompt.
This happened with micromamba, which also lacks the `conda info --json`, `bin/activate`, and `conda config --show channels` behavior Nextflow and the nf-core utils subworkflow rely on, so use Miniforge instead.

## Installing conda via Miniforge

Miniforge provides a real `conda`.

1. Pick the installer for the platform (`uname -sm`), for example `Miniforge3-Linux-x86_64.sh`, `Miniforge3-Linux-aarch64.sh`, or `Miniforge3-MacOSX-arm64.sh`.
   Download it and its checksum from the latest release at `https://github.com/conda-forge/miniforge/releases` into the scratch directory.
   The checksum file is named with the version, for example `Miniforge3-26.9.4-1-Linux-aarch64.sh.sha256`.
   Verify it with `sha256sum` (or `shasum -a 256` on macOS).
   The installer is a self-extracting script with a binary payload, so read its header and options and rely on the checksum.
2. Install in batch mode, which does not modify shell rc files: `bash <installer> -b -p $HOME/miniforge3`.
3. Symlink only the `conda` command, so the base environment is never auto-activated: `ln -sf $HOME/miniforge3/bin/conda ~/.local/bin/conda`.
4. Create `~/.condarc` with both channels in this order, because the nf-core check requires them:

   ```yaml
   channels:
     - conda-forge
     - bioconda
   ```

5. Point Nextflow's environment cache at local disk for conda runs: `export NXF_CONDA_CACHEDIR=$HOME/.nxf-conda-cache`.
   Environments are not relocatable, so do not rename or move this directory after it is populated.
   Do not put it on a network or host-mounted path.

The first conda run builds one environment per module (a few minutes); later runs reuse the cache.

## Testing notes

- Run `uv run scripts/generate_test_data.py` first; the synthetic profiles are not tracked.
- Under conda, the `-profile test` snapshot differs only in software versions (for example `pyarrow`); do not update snapshots from conda runs.
- When running a long test in the background, capture the test command's own exit code (for example `nf-test test ...; echo "EXIT=$?"`).
  Some harnesses report only the exit code of the last command, so a trailing `echo` can hide a failure.
- `nf-test test file.nf.test@hash` keeps only one test per file when several are given; run whole files instead.
