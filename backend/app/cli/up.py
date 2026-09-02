"""Start the API, the worker, and the UI from one terminal.

``scinet-stop`` exists because processes this project did *not* start have to
be found and killed without taking the terminal with them. This command is its
complement: it spawns all three processes itself, so it holds real handles and
never has to hunt. Stopping is therefore trivial here — signal the children we
own — and the hard cases stay where they belong, in ``stop.py``.

Foreground by design. Ctrl+C stops everything; closing the terminal stops
everything. A supervisor that daemonizes needs a PID file, a status command
and a log directory, which is an init system — and anyone who wants one has
one. What this replaces is three terminals and the order you must start them
in, which is the actual first-run failure: a UI with no API behind it renders
a black screen and a shell command.

    uv run scinet-up              # API + worker + UI
    uv run scinet-up --no-worker  # serve the existing map only
    uv run scinet-up --dry-run    # print what would start
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import IO

from app.core.config import get_settings
from app.core.model_store import _port_is_open

#: How long to give the API to answer on its port before declaring the start
#: failed. Cold start measures ~0.6 s; the margin is for a slow disk.
API_STARTUP_TIMEOUT_SECONDS = 30.0

#: Grace between SIGTERM and SIGKILL at shutdown — matches stop.py's grace.
TERMINATE_GRACE_SECONDS = 5.0

VITE_PORT = 5173

BACKEND_DIR = Path(__file__).resolve().parents[2]
FRONTEND_DIR = BACKEND_DIR.parent / "frontend"


@dataclass(frozen=True)
class ProcSpec:
    """One child process: what to call it, what to run, and where."""

    name: str
    argv: tuple[str, ...]
    cwd: Path


def build_specs(
    *,
    api_port: int,
    host: str,
    with_worker: bool = True,
    with_ui: bool = True,
    reload: bool = False,
    bun_path: str | None = None,
) -> tuple[list[ProcSpec], list[str]]:
    """Decide what to start. Returns (specs, warnings).

    Pure so it can be tested without spawning anything. The UI entry is
    dropped — with a warning, not an error — when bun is absent or the
    frontend checkout is missing: an API and a worker are still a working
    system, and refusing to start it over a missing dev server helps nobody.
    """
    specs = [
        ProcSpec(
            name="api",
            argv=(
                sys.executable,
                "-m",
                "uvicorn",
                "app.main:app",
                "--host",
                host,
                "--port",
                str(api_port),
                *(["--reload"] if reload else []),
            ),
            cwd=BACKEND_DIR,
        )
    ]
    if with_worker:
        specs.append(
            ProcSpec(
                name="worker",
                argv=(sys.executable, "-m", "app.workers.runner"),
                cwd=BACKEND_DIR,
            )
        )

    warnings: list[str] = []
    if with_ui:
        if bun_path is None:
            warnings.append(
                "bun is not installed; starting without the UI "
                "(the API still serves the map at /api/graph)"
            )
        elif not FRONTEND_DIR.is_dir():
            warnings.append(f"no frontend checkout at {FRONTEND_DIR}; skipping the UI")
        else:
            specs.append(
                ProcSpec(name="ui", argv=(bun_path, "run", "dev"), cwd=FRONTEND_DIR)
            )
    return specs, warnings


def port_conflict(api_port: int) -> str | None:
    """A message if the API port is taken, else None.

    Refusing beats binding a second uvicorn to fail a moment later with a
    less helpful error — and the likeliest cause is a SciNet that is already
    running, which has a one-command fix.
    """
    if _port_is_open("127.0.0.1", api_port):
        return (
            f"something is already listening on :{api_port} — is SciNet "
            f"already running? `uv run scinet-stop` stops it."
        )
    return None


def _pump(name: str, pipe: IO[str], write: Callable[[str], object] = print) -> None:
    """Prefix every child line so three logs interleave legibly."""
    for line in iter(pipe.readline, ""):
        write(f"[{name}] {line.rstrip()}")


def _spawn(spec: ProcSpec) -> subprocess.Popen[str]:
    proc = subprocess.Popen(
        list(spec.argv),
        cwd=spec.cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    threading.Thread(target=_pump, args=(spec.name, proc.stdout), daemon=True).start()
    return proc


def _terminate_all(children: dict[str, subprocess.Popen[str]]) -> None:
    for proc in children.values():
        if proc.poll() is None:
            proc.terminate()
    deadline = time.monotonic() + TERMINATE_GRACE_SECONDS
    for name, proc in children.items():
        remaining = max(0.1, deadline - time.monotonic())
        try:
            proc.wait(timeout=remaining)
        except subprocess.TimeoutExpired:
            print(f"[up] {name} ignored SIGTERM; killing it")
            proc.kill()


def _watch(children: dict[str, subprocess.Popen[str]]) -> int:
    """Block until a child exits. One dead child means the app is broken;
    keeping the survivors up would hide that behind a UI that half-works."""
    while True:
        for name, proc in children.items():
            code = proc.poll()
            if code is not None:
                print(f"[up] {name} exited with {code}; stopping the rest")
                return 1 if code else 0
        time.sleep(0.5)


def main(argv: list[str] | None = None) -> int:
    # print() block-buffers when stdout is a pipe, and a supervisor whose log
    # arrives minutes late reads as a supervisor that hung. (pytest's capsys
    # replaces stdout with an object that has no reconfigure — hence the guard.)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(line_buffering=True)
    settings = get_settings()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api-port", type=int, default=settings.port)
    parser.add_argument("--no-worker", action="store_true", help="serve only")
    parser.add_argument("--no-ui", action="store_true", help="API and worker only")
    parser.add_argument(
        "--reload", action="store_true", help="uvicorn --reload for API development"
    )
    parser.add_argument("--dry-run", action="store_true", help="print, start nothing")
    args = parser.parse_args(argv)

    specs, warnings = build_specs(
        api_port=args.api_port,
        host=settings.host,
        with_worker=not args.no_worker,
        with_ui=not args.no_ui,
        reload=args.reload,
        bun_path=shutil.which("bun"),
    )
    for note in warnings:
        print(f"[up] {note}")

    if args.dry_run:
        for spec in specs:
            print(f"[{spec.name}] would run: {' '.join(spec.argv)}  (in {spec.cwd})")
        return 0

    conflict = port_conflict(args.api_port)
    if conflict:
        print(f"[up] {conflict}", file=sys.stderr)
        return 1

    children = {spec.name: _spawn(spec) for spec in specs}
    print(f"[up] started: {', '.join(children)}")

    deadline = time.monotonic() + API_STARTUP_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        if _port_is_open("127.0.0.1", args.api_port):
            break
        api = children.get("api")
        if api is not None and api.poll() is not None:
            break
        time.sleep(0.3)
    if _port_is_open("127.0.0.1", args.api_port):
        url = (
            f"http://localhost:{VITE_PORT}"
            if "ui" in children
            else f"http://127.0.0.1:{args.api_port}/api/system"
        )
        print(f"[up] SciNet is up — open {url}   (Ctrl+C stops everything)")
    else:
        print("[up] the API did not come up; shutting the rest down", file=sys.stderr)
        _terminate_all(children)
        return 1

    try:
        exit_code = _watch(children)
    except KeyboardInterrupt:
        print("\n[up] stopping…")
        exit_code = 0
    _terminate_all(children)
    print("[up] all processes stopped")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
