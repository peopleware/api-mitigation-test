#!/bin/sh
set -eu
app_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
if [ -n "${GITHUB_WORKSPACE:-}" ]; then
    cd "$GITHUB_WORKSPACE"
elif [ -n "${BITBUCKET_CLONE_DIR:-}" ]; then
    cd "$BITBUCKET_CLONE_DIR"
fi
if [ "$#" -gt 0 ]; then
    exec python "$app_dir/runner.py" "$@"
fi
: "${BITBUCKET_BUILD_NUMBER:?Direct Docker use requires --config}"
: "${CONFIG:?CONFIG must name the project-owned config file}"
exec python "$app_dir/runner.py" --config "$CONFIG"
