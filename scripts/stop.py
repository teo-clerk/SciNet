#!/usr/bin/env python
"""Wrapper around ``scinet-stop`` for people who reach for the script.

The implementation lives in ``backend/app/cli/stop.py`` so that it can be
imported and tested, and so ``uv run scinet-stop`` works from the backend.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.cli.stop import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
