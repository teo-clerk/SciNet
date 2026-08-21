#!/usr/bin/env bash
# Stop the SciNet API, worker, and Vite dev server.
# Thin wrapper: the logic lives in stop.py so it works on Windows too.
set -euo pipefail
cd "$(dirname "$0")/../backend"
exec uv run python ../scripts/stop.py "$@"
