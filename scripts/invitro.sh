#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (C) 2026 unlucio and the Bespok3d contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# Runs the invitro suite against a REAL printer. The read-only tier runs by default; set
# B3D_INVITRO_MUTATE=1 to also run the tests that change printer state (spool picks, the
# helper's config, Klipper restarts). The printer address comes from B3D_HIL_HOST.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
B3D_TOOLING="${B3D_TOOLING:-$REPO_ROOT/lib_bespok3d/tooling}"
PYTEST="$B3D_TOOLING/.venv-tools/bin/pytest"

if [ -z "${B3D_HIL_HOST:-}" ]; then
    echo "B3D_HIL_HOST is not set. Point it at the printer to test, for example:" >&2
    echo "  B3D_HIL_HOST=<printer-address> bash scripts/invitro.sh" >&2
    exit 1
fi

if [ ! -x "$PYTEST" ]; then
    echo "The gate's python tools are not provisioned yet. Run this once, then retry:" >&2
    echo "  bash scripts/check.sh" >&2
    exit 1
fi

cd "$REPO_ROOT/spoolman" || exit 1

if [ "${B3D_INVITRO_MUTATE:-0}" = "1" ]; then
    echo "invitro suite against $B3D_HIL_HOST: read-only + mutating tiers"
    exec "$PYTEST" -c "$B3D_TOOLING/pytest.ini" --rootdir . --tb=short -q tests_invitro
fi

echo "invitro suite against $B3D_HIL_HOST: read-only tier (B3D_INVITRO_MUTATE=1 adds mutating)"
exec "$PYTEST" -c "$B3D_TOOLING/pytest.ini" --rootdir . --tb=short -q \
    -m "not mutating" tests_invitro
