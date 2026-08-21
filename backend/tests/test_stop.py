"""Stopping the background processes without stopping ourselves.

The bug this guards against has happened repeatedly in this repository: a
process scan matches the shell that launched it, and the stop command kills the
terminal it was typed into. Every rule here exists because some simpler version
of it did that.
"""

from __future__ import annotations

from app.cli import stop


def test_a_running_worker_is_matched():
    assert stop.is_worker("/usr/bin/python -m app.workers.runner")
    assert stop.is_worker("./.venv/bin/python3.12 -m app.workers.runner")


def test_a_command_that_merely_names_the_worker_is_not_one():
    """`pkill -f app.workers.runner` matches its own shell. This must not."""
    assert not stop.is_worker("pkill -f app.workers.runner")
    assert not stop.is_worker("grep -r app.workers.runner backend/")
    assert not stop.is_worker("vim backend/app/workers/runner.py")


def test_the_executable_settles_it_where_the_os_will_say():
    """A shell whose arguments name the worker is still a shell.

    The command line cannot distinguish these two — both contain the module
    path and the word python — and on Linux the binary behind the PID can.
    """
    watcher = "bash -c 'until pgrep -f \"python -m app.workers.runner\"; do :; done'"

    assert stop.is_worker(watcher)  # command line alone is fooled
    assert not stop.is_worker(watcher, executable="/usr/bin/bash")
    assert stop.is_worker(
        "python -m app.workers.runner", executable="/usr/bin/python3.12"
    )


def test_selection_uses_the_executable_when_one_is_known():
    table = [
        (100, "python -m app.workers.runner"),
        (200, "bash -c 'python -m app.workers.runner'"),
    ]
    binaries = {100: "/usr/bin/python3.12", 200: "/usr/bin/bash"}

    selected = stop.select_workers(table, protected=set(), executable=binaries.get)

    assert selected == {100}


def test_an_unrelated_python_process_is_not_a_worker():
    assert not stop.is_worker("python -m http.server")
    assert not stop.is_worker("python -m uvicorn app.main:app")


def test_our_own_lineage_is_never_selected():
    """Even a process table that looks exactly like a worker."""
    table = [
        (100, "python -m app.workers.runner"),
        (200, "python -m app.workers.runner"),
    ]

    unknown = lambda _pid: ""  # noqa: E731 - no executable available

    assert stop.select_workers(table, set(), unknown) == {100, 200}
    assert stop.select_workers(table, {100}, unknown) == {200}
    assert stop.select_workers(table, {100, 200}, unknown) == set()


def test_terminate_refuses_to_signal_a_protected_pid(monkeypatch):
    signalled: list[int] = []
    monkeypatch.setattr(stop, "_own_lineage", lambda: {4242})
    monkeypatch.setattr(stop, "_alive", lambda pid: False)
    monkeypatch.setattr(stop.os, "kill", lambda pid, sig: signalled.append(pid))

    stopped = stop.terminate({4242, 99}, dry_run=False)

    assert signalled == [99]
    assert stopped == [99]


def test_dry_run_signals_nothing(monkeypatch):
    signalled: list[int] = []
    monkeypatch.setattr(stop, "_own_lineage", lambda: set())
    monkeypatch.setattr(stop.os, "kill", lambda pid, sig: signalled.append(pid))

    stopped = stop.terminate({99}, dry_run=True)

    assert signalled == []
    assert stopped == [99]


def test_liveness_is_a_query_on_windows_not_a_kill(monkeypatch):
    """os.kill(pid, 0) terminates the process on Windows rather than probing it.

    Every value but the two console-control signals reaches TerminateProcess,
    so the portable-looking probe is destructive on exactly one platform.
    """
    monkeypatch.setattr(stop, "IS_WINDOWS", True)
    monkeypatch.setattr(stop, "_run", lambda cmd: "python.exe   4242 Console")

    def explode(*_args):
        raise AssertionError("os.kill must not be called to test liveness")

    monkeypatch.setattr(stop.os, "kill", explode)

    assert stop._alive(4242)
    monkeypatch.setattr(stop, "_run", lambda cmd: "INFO: No tasks are running")
    assert not stop._alive(4242)
