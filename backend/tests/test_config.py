"""Settings: where a library lives.

A second library on the same machine — a benchmark corpus beside a personal
one — must be one variable away, and must never share a database with the
first. These tests pin which paths follow ``data_dir`` and which do not.
"""

from __future__ import annotations

from pathlib import Path

from app.core.config import REPO_ROOT, Settings


def test_defaults_sit_under_the_repo_data_folder():
    s = Settings()

    assert s.data_dir == REPO_ROOT / "data"
    assert s.library_dir == REPO_ROOT / "data" / "library"
    assert s.db_path == REPO_ROOT / "data" / "scinet.db"
    assert s.models_dir == REPO_ROOT / "data" / "models"


def test_library_paths_follow_data_dir(tmp_path):
    s = Settings(data_dir=tmp_path / "demo")

    assert s.library_dir == tmp_path / "demo" / "library"
    assert s.markdown_dir == tmp_path / "demo" / "markdown"
    assert s.vectors_dir == tmp_path / "demo" / "vectors"
    assert s.db_path == tmp_path / "demo" / "scinet.db"


def test_models_do_not_follow_data_dir(tmp_path):
    """Models are not part of a library; moving one must not re-download them."""
    s = Settings(data_dir=tmp_path / "demo")

    assert s.models_dir == REPO_ROOT / "data" / "models"


def test_an_explicit_path_wins_over_the_derived_one(tmp_path):
    s = Settings(data_dir=tmp_path / "demo", library_dir=tmp_path / "elsewhere")

    assert s.library_dir == tmp_path / "elsewhere"
    assert s.db_path == tmp_path / "demo" / "scinet.db"


def test_environment_relocates_a_library_the_same_way(monkeypatch, tmp_path):
    monkeypatch.setenv("SCINET_DATA_DIR", str(tmp_path / "demo"))

    s = Settings()

    assert s.db_path == tmp_path / "demo" / "scinet.db"
    assert s.library_dir == tmp_path / "demo" / "library"


def test_a_relative_data_dir_resolves_against_the_repo():
    s = Settings(data_dir=Path("data/demo"))

    assert s.data_dir == (REPO_ROOT / "data" / "demo").resolve()
    assert s.db_path == (REPO_ROOT / "data" / "demo" / "scinet.db").resolve()
