#!/usr/bin/env bash
# This plugin's own gate: it must pass from this repo's root, with no sibling repo cloned except
# lib_bespok3d. Exits non-zero on any failure.
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# The shared gate helpers and the detectors that enforce a workspace-wide rule live in one place.
# See lib_bespok3d/tooling/README.md. This is the only line that knows where they are.
B3D_TOOLING="${B3D_TOOLING:-$REPO_ROOT/lib_bespok3d/tooling}"
# shellcheck source=/dev/null
. "$B3D_TOOLING/gate-lib.sh"

cd "$REPO_ROOT" || exit 1

PLUGIN_DIR="$REPO_ROOT/spoolman"
SPOOL_EXTRAS="files/klipper/klippy/extras/spoolman"

echo ""
echo "spoolman-klipper-helper gate"

b3d_python_tools

run_check "pytest"  pytest_in_dir "$PLUGIN_DIR" tests
run_check "ruff"    ruff_in_dir "$PLUGIN_DIR" files tests
# The moonraker proxy imports moonraker itself, so it is ruff-only; the pure modules carry the type
# coverage.
run_check "mypy"    mypy_in_dir "$PLUGIN_DIR" "$SPOOL_EXTRAS/logs.py" "$SPOOL_EXTRAS/u1_tools.py"

workflow_pinning_check "$REPO_ROOT"
em_dash_check "$REPO_ROOT"
shellcheck_repo "$REPO_ROOT"

gate_summary || exit 1
