#!/bin/bash
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")"

wave_options="--build-template conda/pixi:v1 --freeze"
wave --conda-file environment.yml $wave_options
wave --conda-file environment.yml $wave_options --singularity
