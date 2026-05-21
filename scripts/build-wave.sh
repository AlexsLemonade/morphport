#!/bin/bash
set -euo pipefail
# Builds a wave file for a given module using the environment.yml file
# Usage: ./build-wave.sh <module_path>
# Note that your TOWER_ACCESS_TOKEN environment variable must be set to a valid token for the build to succeed
# If running with 1Password, run via `op run -- ./build-wave.sh <module_path>` so that the token is resolved from your vault

build_template="conda/pixi:v1"

# Check if the correct number of arguments is provided
if [ "$#" -ne 1 ]; then
    echo "Usage: $0 <module_path>"
    exit 1
fi

module_path="$1"

# Check if the module path exists and has an environment.yml file
if [ ! -d "$module_path" ]; then
    echo "Error: Module path '$module_path' does not exist."
    exit 1
fi
if [ ! -f "$module_path/environment.yml" ]; then
    echo "Error: No environment.yml file found in '$module_path'."
    exit 1
fi

# Make sure the Tower token is set and resolved (not a literal op:// reference,
# which would indicate the script was not run under `op run `)
if [ -z "${TOWER_ACCESS_TOKEN:-}" ]; then
    echo "Error: TOWER_ACCESS_TOKEN is not set." >&2
    exit 1
fi
if [[ "$TOWER_ACCESS_TOKEN" == op://* ]]; then
    echo "Error: TOWER_ACCESS_TOKEN is an unresolved 1Password reference ('${TOWER_ACCESS_TOKEN}')." >&2
    echo "Hint: run via 'op run -- $0 ...' so 'op' resolves the secret." >&2
    exit 1
fi

# run wave build commands for docker and singularity

wave_options="--build-template ${build_template} --freeze"
docker_image=$(wave --conda-file "$module_path/environment.yml" ${wave_options})
singularity_image=$(wave --conda-file "$module_path/environment.yml" ${wave_options} --singularity)
echo "Docker image built: $docker_image"
echo "Singularity image built: $singularity_image"

echo "For including in a process, use the following lines:"
cat <<EOF
    container {
        workflow.containerEngine == 'singularity' && !task.ext.singularity_pull_docker_container
            ? '${singularity_image}'
            : '${docker_image}'
    }
EOF
