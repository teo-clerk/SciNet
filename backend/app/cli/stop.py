"""Stop everything SciNet leaves running.

Three processes outlive a terminal: the API on :8000, the Vite dev server on
:5173, and the worker, which binds nothing at all and so cannot be found by
port. Leaving any of them behind is not cosmetic — a second worker contends
with the first for the SQLite write lock, and a stale API serves routes that no
longer exist in the code you are editing.

Processes are matched by the port they *listen on* and by their command line,
never by a shell pattern. ``pkill -f uvicorn`` matches the shell running the
pkill, which is how this repository's own tooling repeatedly killed the
terminal it was typed into.

    uv run scinet-stop            # stop everything
    uv run scinet-stop --dry-run  # show what would be stopped

``scripts/stop.sh`` and ``scripts/stop.py`` are thin wrappers around this.
"""

from __future__ import annotations

import argparse
import os
import platform
import re
import signal
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path

API_PORT = 8000
VITE_PORT = 5173
# The worker binds no socket, so it is identified by what it is running.
WORKER_PATTERN = re.compile(r"app\.workers\.runner")
TERM_GRACE_SECONDS = 5.0
IS_WINDOWS = platform.system() == "Windows"


def _own_lineage() -> set[int]:
    """This process and its ancestors, which must never be killed."""
    lineage = {os.getpid(), os.getppid()}
    if not IS_WINDOWS:
        pid = os.getppid()
        for _ in range(8):
            try:
                stat = Path(f"/proc/{pid}/stat").read_text()
                pid = int(stat.rsplit(")", 1)[1].split()[1])
            except (OSError, ValueError, IndexError):
                break
            if pid <= 1:
                break
            lineage.add(pid)
    return lineage


def _run(command: list[str]) -> str:
    try:
        done = subprocess.run(command, capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.SubprocessError):
        return ""
    return done.stdout


def listeners(port: int) -> set[int]:
    """PIDs listening on ``port``."""
    pids: set[int] = set()

    if IS_WINDOWS:
        for line in _run(["netstat", "-ano", "-p", "tcp"]).splitlines():
            parts = line.split()
            if len(parts) >= 5 and parts[3].upper() == "LISTENING":
                if parts[1].rsplit(":", 1)[-1] == str(port):
                    pids.add(int(parts[4]))
        return pids

    out = _run(["lsof", "-ti", f"tcp:{port}", "-sTCP:LISTEN"])
    if out.strip():
        return {int(x) for x in out.split()}

    # lsof is not installed everywhere; ss is standard on modern Linux.
    out = _run(["ss", "-ltnpH", f"sport = :{port}"])
    pids.update(int(m) for m in re.findall(r"pid=(\d+)", out))
    return pids


def _process_table() -> list[tuple[int, str]]:
    """(pid, command line) for every process we can see."""
    rows: list[tuple[int, str]] = []

    if IS_WINDOWS:
        out = _run(["wmic", "process", "get", "ProcessId,CommandLine", "/format:csv"])
        for line in out.splitlines():
            fields = line.split(",")
            if len(fields) >= 3 and fields[-1].strip().isdigit():
                rows.append((int(fields[-1]), ",".join(fields[1:-1])))
        if rows:
            return rows
        # wmic was removed in recent Windows releases. CIM is the replacement
        # and is present wherever PowerShell is, which is everywhere this runs.
        out = _run(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                "Get-CimInstance Win32_Process | "
                'ForEach-Object { "$($_.ProcessId)`t$($_.CommandLine)" }',
            ]
        )
        for line in out.splitlines():
            pid, _, command = line.partition("\t")
            if pid.strip().isdigit():
                rows.append((int(pid.strip()), command))
        return rows

    if Path("/proc").is_dir():
        for entry in Path("/proc").iterdir():
            if not entry.name.isdigit():
                continue
            try:
                raw = (entry / "cmdline").read_bytes()
            except OSError:
                continue
            rows.append(
                (int(entry.name), raw.replace(b"\0", b" ").decode(errors="replace"))
            )
        return rows

    for line in _run(["ps", "-eo", "pid=,args="]).splitlines():
        line = line.strip()
        pid, _, args = line.partition(" ")
        if pid.isdigit():
            rows.append((int(pid), args))
    return rows


def executable_of(pid: int) -> str:
    """What binary this process is actually running, where the OS will say.

    Linux answers through /proc. Nothing else here does, and the command-line
    test below covers those, but where this *is* available it is the only
    signal a command line that merely mentions the worker cannot forge.
    """
    try:
        return os.readlink(f"/proc/{pid}/exe")
    except OSError:
        return ""


def is_worker(cmdline: str, executable: str = "") -> bool:
    """Is this process running the worker, or only talking about it?

    ``pkill -f app.workers.runner`` matches the shell that typed it, which is
    how this repository's own tooling repeatedly killed the terminal it ran in.
    A command line cannot tell the two apart — a shell watching for the worker
    contains the same words the worker does — so where the operating system
    will name the *binary*, that is the test. Elsewhere, requiring a Python
    interpreter somewhere in the command line excludes the grep, the editor
    with the file open, and this script.
    """
    if not WORKER_PATTERN.search(cmdline):
        return False
    if executable:
        return "python" in Path(executable).name.lower()
    return "python" in cmdline.lower()


def select_workers(
    table: list[tuple[int, str]],
    protected: set[int],
    executable: Callable[[int], str] = executable_of,
) -> set[int]:
    """Worker PIDs from a process table, never including our own lineage."""
    return {
        pid
        for pid, cmdline in table
        if pid not in protected and is_worker(cmdline, executable(pid))
    }


def workers() -> set[int]:
    """PIDs running the SciNet worker, excluding this process's own lineage."""
    return select_workers(_process_table(), _own_lineage())


def describe(pid: int) -> str:
    for candidate, cmdline in _process_table():
        if candidate == pid:
            return " ".join(cmdline.split())[:88]
    return "(gone)"


def terminate(pids: set[int], *, dry_run: bool) -> list[int]:
    """Ask politely, then insist. Returns the PIDs that actually stopped."""
    protected = _own_lineage()
    targets = sorted(pids - protected)
    if dry_run or not targets:
        return targets

    for pid in targets:
        try:
            os.kill(pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError, OSError):
            continue

    deadline = time.monotonic() + TERM_GRACE_SECONDS
    while time.monotonic() < deadline:
        if not any(_alive(pid) for pid in targets):
            return targets
        time.sleep(0.2)

    for pid in targets:
        if _alive(pid):
            try:
                os.kill(pid, signal.SIGKILL if not IS_WINDOWS else signal.SIGTERM)
            except (ProcessLookupError, PermissionError, OSError):
                pass
    return targets


def _alive(pid: int) -> bool:
    """Is this process still running?

    ``os.kill(pid, 0)`` is the portable-looking way to ask and is not portable.
    On Windows ``os.kill`` supports only the two console-control signals; every
    other value, zero included, calls TerminateProcess. So the "probe" would
    kill the process it was asked about — and, after a PID is recycled, some
    unrelated process instead. Windows gets a real query.
    """
    if IS_WINDOWS:
        out = _run(["tasklist", "/FI", f"PID eq {pid}", "/NH"])
        return str(pid) in out

    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # Running, but not ours to signal.
        return True
    except OSError:
        return False
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="report, kill nothing")
    parser.add_argument("--api-port", type=int, default=API_PORT)
    parser.add_argument("--vite-port", type=int, default=VITE_PORT)
    args = parser.parse_args()

    groups = {
        f"API (:{args.api_port})": listeners(args.api_port),
        f"Vite (:{args.vite_port})": listeners(args.vite_port),
        "worker": workers(),
    }

    anything = False
    for label, pids in groups.items():
        stopped = terminate(pids, dry_run=args.dry_run)
        if not stopped:
            print(f"{label}: not running")
            continue
        anything = True
        verb = "would stop" if args.dry_run else "stopped"
        for pid in stopped:
            detail = f"  {describe(pid)}" if args.dry_run else ""
            print(f"{label}: {verb} {pid}{detail}")

    if not anything:
        print("\nnothing was running")
    elif not args.dry_run:
        leaked = [p for pids in groups.values() for p in pids if _alive(p)]
        if leaked:
            print(f"\ncould not stop: {leaked}", file=sys.stderr)
            return 1
        print("\nall SciNet processes stopped; ports are free")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
