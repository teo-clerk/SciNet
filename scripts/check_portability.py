#!/usr/bin/env python
"""Prove the checkout carries its own models, and name anything that does not.

The promise this project makes is that ``data/`` holds everything: zip the
directory, unzip it on another machine, and it runs. Model libraries do not
default to that. Every one of them writes into the user's home directory unless
told otherwise, and when one of them slips through nothing fails — the model
downloads again, into the wrong place, and the only symptom is a checkout that
turns out to be hollow on the machine you moved it to.

So this checks rather than assumes. It reports where each cache variable
points, then looks in the well-known global locations for weights this project
is actually configured to use, which is what distinguishes SciNet leaking into
a home directory from a home directory that simply has models in it.

    uv run python ../scripts/check_portability.py

Exits non-zero if any of this project's models are found outside it.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1] / "backend"
sys.path.insert(0, str(BACKEND))

from app.core.config import get_settings  # noqa: E402
from app.core.model_store import configure_environment  # noqa: E402
from app.core.models_registry import (  # noqa: E402
    huggingface_models,
    ollama_models,
)

#: Environment variables that steer where a library writes its weights. Grouped
#: because a reader wants to know "is anything pointing outside the project",
#: not the full environment.
CACHE_VARIABLES = (
    "HF_HOME",
    "HF_HUB_CACHE",
    "HUGGINGFACE_HUB_CACHE",
    "TRANSFORMERS_CACHE",
    "HF_DATASETS_CACHE",
    "SENTENCE_TRANSFORMERS_HOME",
    "TIKTOKEN_CACHE_DIR",
    "NLTK_DATA",
    "MPLCONFIGDIR",
    "MODEL_CACHE_DIR",
    "TORCH_HOME",
)


def global_cache_roots() -> list[Path]:
    """Where model libraries put things when nobody tells them otherwise."""
    home = Path.home()
    roots = [
        home / ".cache" / "huggingface",
        home / ".cache" / "torch",
        home / ".cache" / "datalab",
        home / ".ollama",
        home / ".torch",
        home / ".keras",
        home / "nltk_data",
        home / ".EasyOCR",
    ]
    # Windows and macOS put the same caches somewhere else entirely.
    if local := os.environ.get("LOCALAPPDATA"):
        roots += [Path(local) / "huggingface", Path(local) / "datalab"]
    roots.append(home / "Library" / "Caches" / "huggingface")
    return [r for r in roots if r.exists()]


def directory_size(path: Path) -> int:
    total = 0
    for entry in path.rglob("*"):
        try:
            if entry.is_file():
                total += entry.stat().st_size
        except OSError:
            continue
    return total


def human(size: int) -> str:
    value = float(size)
    for unit in ("B", "KiB", "MiB", "GiB"):
        if value < 1024 or unit == "GiB":
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} GiB"


def _hf_directory_name(reference: str) -> str:
    """``malteos/scincl`` is cached as ``models--malteos--scincl``."""
    return "models--" + reference.replace("/", "--")


def _weight_dirs(root: Path, reference: str) -> list[Path]:
    """Directories actually holding this model's weights.

    ``.locks`` carries a directory per model with the same name and no weights
    at all, and it outlives the blobs — so matching on the name alone reports a
    model as leaked after its weights have been deleted, and then points the
    operator at a lock directory and calls it safe to delete.
    """
    name = _hf_directory_name(reference)
    return [
        found
        for found in root.rglob(name)
        if ".locks" not in found.parts and any(found.rglob("*"))
    ]


def local_copies(settings) -> set[str]:
    """Which of this project's models are already inside it.

    Checked before anything is recommended for deletion. The HuggingFace layout
    moved between library versions — weights land under ``hf/hub/`` or directly
    under ``hf/`` depending on which variable the installed version honours — so
    this searches rather than assuming a path.
    """
    store = settings.models_dir
    present = set()
    for entry in huggingface_models():
        if _weight_dirs(store, entry.reference):
            present.add(entry.reference)

    manifests = store / "ollama" / "manifests"
    if manifests.exists():
        for entry in ollama_models():
            name = entry.reference.split(":")[0].rsplit("/", 1)[-1]
            if any(manifests.rglob(name)):
                present.add(entry.reference)
    return present


def leaked_models(root: Path) -> list[str]:
    """This project's own models found under ``root``.

    Named exactly. A home directory containing somebody else's models is not a
    leak and must not be reported as one, or the check becomes noise that gets
    ignored — which is worse than not running it.
    """
    found: list[str] = []

    for entry in huggingface_models():
        if _weight_dirs(root, entry.reference):
            found.append(entry.reference)

    manifests = root / "models" / "manifests"
    if manifests.exists():
        for entry in ollama_models():
            name = entry.reference.split(":")[0].rsplit("/", 1)[-1]
            if any(manifests.rglob(name)):
                found.append(entry.reference)

    return found


def main() -> int:
    settings = get_settings()
    settings.ensure_dirs()
    applied = configure_environment(settings)

    project = settings.data_dir.resolve()
    print(f"project data : {project}")
    store_size = human(directory_size(settings.models_dir))
    print(f"model store  : {settings.models_dir}  ({store_size})")
    print()

    print("cache variables")
    stray = []
    for name in CACHE_VARIABLES:
        value = applied.get(name) or os.environ.get(name, "")
        if not value:
            print(f"  {name:<28} (unset)")
            continue
        inside = Path(value).resolve().is_relative_to(project)
        print(f"  {name:<28} {'ok ' if inside else 'OUTSIDE'} {value}")
        if not inside:
            stray.append(name)

    print()
    print("global cache locations")
    leaks: list[tuple[Path, list[str]]] = []
    roots = global_cache_roots()
    if not roots:
        print("  none present")
    for root in roots:
        ours = leaked_models(root)
        marker = "LEAK   " if ours else "unrelated"
        print(f"  {marker} {root}  ({human(directory_size(root))})")
        if ours:
            for reference in ours:
                print(f"            holds {reference}, which this project uses")
            leaks.append((root, ours))

    print()
    # Stated rather than silently tolerated: Surya hardcodes this path for its
    # server lock and log files and offers no setting for it. No weights go
    # there — MODEL_CACHE_DIR governs those — and it is only touched when tier 1
    # runs, which is off by default.
    print("known and accepted: surya writes server locks and logs to")
    print("  ~/.cache/datalab/surya (hardcoded upstream; no weights, no setting)")

    if stray:
        print()
        print(f"FAIL: {len(stray)} cache variable(s) point outside the project")
    if leaks:
        held = local_copies(settings)
        print()
        print(f"FAIL: this project's models are in {len(leaks)} global cache(s).")
        for root, ours in leaks:
            for reference in ours:
                target = next(iter(_weight_dirs(root, reference)), root)
                if reference in held:
                    print(f"  {reference}")
                    print("    duplicate of a copy already inside the project;")
                    print(f"    safe to delete: {target}")
                else:
                    # The only copy. Deleting it would make the project
                    # re-download on next use, which is not what a portability
                    # check should talk anyone into doing.
                    print(f"  {reference}")
                    print("    this is the ONLY copy — the project does not have it.")
                    print("    run scripts/download_models.py first, then re-check.")
    if not stray and not leaks:
        print()
        print("PASS: every model this project uses lives inside it")
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
