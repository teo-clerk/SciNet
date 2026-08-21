#!/usr/bin/env bash
# Stop the API, the Vite dev server and the worker.
#
# A wrapper around `uv run scinet-stop`, which is the same thing without the
# need to be in backend/. See backend/app/cli/stop.py for what it matches on
# and why it never matches itself.
set -euo pipefail
cd "$(dirname "$0")/../backend"
exec uv run scinet-stop "$@"
