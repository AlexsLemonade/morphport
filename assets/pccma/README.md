# PCCMA metadata

Metadata for the Pediatric Cancer Cell Morphology Atlas (PCCMA) atlas and REPO1 compound screen, derived from the upstream WayScience repositories.
These files approximate what the Cell Painting Gallery (`cpg0041-pccma`) will hold, to guide development and serve as realistic example inputs.

The files here are built from copies of the upstream files in `source/`, which are not tracked by git; see `source/README.md` for the upstream files and the commits they come from.

To rebuild the reference files, run the following commands from the repository root.
The platemaps are built from `cell_lines.csv` and `repo1_compounds.csv`, so run the scripts in this order.
The platemaps in `platemaps/` are not tracked by git, so build them locally before using them.

```bash
# download the upstream files to source/
uv run assets/pccma/scripts/build_source.py
# build the reference files from source/
uv run assets/pccma/scripts/build_cell_lines.py
uv run assets/pccma/scripts/build_compounds.py
uv run assets/pccma/scripts/build_platemaps.py
```

The upstream repositories are public, so `build_source.py` needs no GitHub token.
If you run into GitHub's rate limits, set the `GITHUB_TOKEN` or `GH_TOKEN` environment variable to a GitHub token, which the script sends with each request.
For example, with the GitHub CLI:

```bash
GITHUB_TOKEN="$(gh auth token)" uv run assets/pccma/scripts/build_source.py
```

Or with a token stored in 1Password:

```bash
GITHUB_TOKEN="op://<vault>/<item>/<field>" op run -- uv run assets/pccma/scripts/build_source.py
```

## License

The upstream metadata come from [WayScience/pccma](https://github.com/WayScience/pccma) and [WayScience/pccma_repo1_screen_image_profiling](https://github.com/WayScience/pccma_repo1_screen_image_profiling).
Both repositories are distributed under the following license, which applies to the metadata files here that are derived from them.

```text
BSD 3-Clause License

Copyright (c) 2026, The Way Lab

Redistribution and use in source and binary forms, with or without
modification, are permitted provided that the following conditions are met:

1. Redistributions of source code must retain the above copyright notice, this
   list of conditions and the following disclaimer.

2. Redistributions in binary form must reproduce the above copyright notice,
   this list of conditions and the following disclaimer in the documentation
   and/or other materials provided with the distribution.

3. Neither the name of the copyright holder nor the names of its
   contributors may be used to endorse or promote products derived from
   this software without specific prior written permission.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE
FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR
SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER
CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY,
OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
```
