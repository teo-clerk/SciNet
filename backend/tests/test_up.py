"""The supervisor must decide correctly before it spawns anything.

`scinet-up` owns three child processes. Everything that can be got wrong
without spawning — which commands to run, what to skip when bun is absent,
refusing a port that is already taken — is a pure function here, tested
without ever starting a process. The one failure these tests exist to
prevent: a machine without bun being refused an API + worker that would
have worked fine.
"""

from __future__ import annotations

import io

from app.cli import up
from app.cli.up import ProcSpec, build_specs, port_conflict

# --- what to start -----------------------------------------------------------


def test_all_three_processes_by_default() -> None:
    specs, warnings = build_specs(
        api_port=8000, host="127.0.0.1", bun_path="/usr/bin/bun"
    )
    assert [s.name for s in specs] == ["api", "worker", "ui"]
    assert warnings == []


def test_the_api_command_carries_host_and_port() -> None:
    specs, _ = build_specs(api_port=9000, host="127.0.0.1", bun_path=None)
    api = specs[0]
    assert "uvicorn" in api.argv
    assert "9000" in api.argv and "127.0.0.1" in api.argv
    assert "--reload" not in api.argv


def test_reload_is_opt_in() -> None:
    specs, _ = build_specs(api_port=8000, host="127.0.0.1", reload=True, bun_path=None)
    assert "--reload" in specs[0].argv


def test_missing_bun_drops_the_ui_with_a_warning_not_an_error() -> None:
    specs, warnings = build_specs(api_port=8000, host="127.0.0.1", bun_path=None)
    assert [s.name for s in specs] == ["api", "worker"]
    assert len(warnings) == 1 and "bun" in warnings[0]


def test_a_missing_frontend_checkout_also_drops_the_ui(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(up, "FRONTEND_DIR", tmp_path / "not-there")
    specs, warnings = build_specs(
        api_port=8000, host="127.0.0.1", bun_path="/usr/bin/bun"
    )
    assert [s.name for s in specs] == ["api", "worker"]
    assert len(warnings) == 1 and "frontend" in warnings[0]


def test_worker_and_ui_can_each_be_declined() -> None:
    specs, _ = build_specs(
        api_port=8000,
        host="127.0.0.1",
        with_worker=False,
        with_ui=False,
        bun_path="/usr/bin/bun",
    )
    assert [s.name for s in specs] == ["api"]


def test_the_worker_runs_the_module_not_a_script() -> None:
    # `python -m app.workers.runner` from backend/ — matching the documented
    # command, so the supervisor and the docs cannot drift apart.
    specs, _ = build_specs(api_port=8000, host="127.0.0.1", bun_path=None)
    worker = specs[1]
    assert worker.argv[-2:] == ("-m", "app.workers.runner")
    assert worker.cwd == up.BACKEND_DIR


# --- the port check ----------------------------------------------------------


def test_a_taken_port_is_refused_with_the_way_out(monkeypatch) -> None:
    monkeypatch.setattr(up, "_port_is_open", lambda host, port: True)
    message = port_conflict(8000)
    assert message is not None and "scinet-stop" in message


def test_a_free_port_raises_no_objection(monkeypatch) -> None:
    monkeypatch.setattr(up, "_port_is_open", lambda host, port: False)
    assert port_conflict(8000) is None


# --- log multiplexing --------------------------------------------------------


def test_child_lines_are_prefixed_with_their_process_name() -> None:
    out: list[str] = []
    up._pump("api", io.StringIO("started\nlistening\n"), write=out.append)
    assert out == ["[api] started", "[api] listening"]


# --- dry run -----------------------------------------------------------------


def test_dry_run_prints_commands_and_starts_nothing(monkeypatch, capsys) -> None:
    spawned: list[ProcSpec] = []
    monkeypatch.setattr(up, "_spawn", lambda spec: spawned.append(spec))
    monkeypatch.setattr(up.shutil, "which", lambda name: None)
    assert up.main(["--dry-run"]) == 0
    assert spawned == []
    printed = capsys.readouterr().out
    assert "would run" in printed and "uvicorn" in printed
