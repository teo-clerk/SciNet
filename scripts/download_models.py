#!/usr/bin/env python
"""Provision SciNet's models into the project's own store.

SciNet does not use the machine's system-wide models. Everything it needs is
downloaded into ``SCINET_MODELS_DIR`` (``data/models/`` by default) and served
from there, so a checkout carries its models with it.

    uv run python ../scripts/download_models.py            # download what is missing
    uv run python ../scripts/download_models.py --measure  # download, then measure VRAM
    uv run python ../scripts/download_models.py --check    # report only
    uv run python ../scripts/download_models.py --role vision

``--measure`` is the interesting mode. Download size does not predict resident
footprint: qwen2.5vl:7b is 5.56 GiB on disk and 13.3 GiB resident. Ollama does
not error when a model exceeds VRAM, it silently serves it from system RAM at
roughly twenty times the latency, so the only way to know a model fits is to
load it and read back ``size_vram``.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1] / "backend"
sys.path.insert(0, str(BACKEND))

from app.core.config import get_settings  # noqa: E402
from app.core.model_store import (  # noqa: E402
    PrivateOllama,
    bin_dir,
    configure_environment,
    hf_dir,
    llama_server_path,
    ollama_dir,
    provision_llamacpp,
)
from app.core.models_registry import (  # noqa: E402
    REGISTRY,
    VRAM_BUDGET_MIB,
    ModelEntry,
    Role,
    Runtime,
    total_disk_mib,
)

WARMUP_PROMPT = "Reply with the single word: ready."


def human(mib: int | None) -> str:
    if mib is None:
        return "     ?"
    return f"{mib / 1024:5.2f}G"


def print_manifest() -> None:
    print(f"{'role':<8} {'model':<30} {'quant':<18} {'disk':>7} {'vram':>7}")
    print("-" * 74)
    for e in REGISTRY:
        print(
            f"{e.role.value:<8} {e.reference:<30} {e.quantization:<18} "
            f"{human(e.disk_mib):>7} {human(e.vram_mib):>7}"
        )
    print("-" * 74)
    print(f"{'':<8} {'total download':<30} {'':<18} {human(total_disk_mib()):>7}")
    print()


def ensure_ollama(entry: ModelEntry, server: PrivateOllama, check_only: bool) -> bool:
    present = entry.reference in server.installed()
    if present:
        print(f"  [ok]      {entry.reference} already in the project store")
        return True
    if check_only:
        print(f"  [missing] {entry.reference}")
        return False

    print(f"  [pull]    {entry.reference} -> {ollama_dir()}")
    last = ""

    def progress(line: str) -> None:
        nonlocal last
        if line and line != last:
            print(f"            {line[:100]}", flush=True)
            last = line

    server.pull(entry.reference, on_progress=progress)
    return entry.reference in server.installed()


def ensure_huggingface(entry: ModelEntry, check_only: bool) -> bool:
    """Fetch HF weights into the project's cache."""
    cache = hf_dir()
    try:
        from huggingface_hub import snapshot_download
    except ImportError:
        print(f"  [skip]    {entry.reference}: huggingface_hub not installed")
        return False

    # Two layouts, because two libraries write here — see preflight._hf_present.
    directory = f"models--{entry.reference.replace('/', '--')}"
    if (cache / "hub" / directory).exists() or (cache / directory).exists():
        print(f"  [ok]      {entry.reference} already in {cache.name}/")
        return True
    if check_only:
        print(f"  [missing] {entry.reference}")
        return False

    print(f"  [fetch]   {entry.reference} -> {cache}")
    try:
        snapshot_download(entry.reference, cache_dir=str(cache / "hub"))
    except Exception as exc:  # noqa: BLE001 - a gated or renamed repo is reportable
        print(f"  [fail]    {entry.reference}: {exc}")
        return False
    return True


def measure(entry: ModelEntry, server: PrivateOllama) -> tuple[int, int] | None:
    """Load the model and read back what actually landed in VRAM."""
    import httpx

    print(f"  measuring {entry.reference} ...", end="", flush=True)
    started = time.perf_counter()
    try:
        httpx.post(
            f"{server.url}/api/generate",
            json={
                "model": entry.reference,
                "prompt": WARMUP_PROMPT,
                "stream": False,
                "keep_alive": "60s",
            },
            timeout=600.0,
        ).raise_for_status()
    except Exception as exc:  # noqa: BLE001
        print(f" failed: {exc}")
        return None

    total, vram = server.resident_footprint_mib(entry.reference)
    elapsed = time.perf_counter() - started
    server.unload(entry.reference)

    on_gpu = (100 * vram / total) if total else 0
    verdict = "GPU" if on_gpu > 90 else ("PARTIAL" if on_gpu > 0 else "CPU-ONLY")
    print(
        f" total={human(total)} vram={human(vram)} ({on_gpu:.0f}% on GPU) "
        f"{verdict}  [{elapsed:.1f}s]"
    )
    return total, vram


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="report only")
    parser.add_argument("--measure", action="store_true", help="measure resident VRAM")
    parser.add_argument("--role", choices=[r.value for r in Role], help="one role only")
    parser.add_argument(
        "--extra",
        action="append",
        default=[],
        help="also pull this ollama model (repeatable)",
    )
    args = parser.parse_args()

    settings = get_settings()
    settings.ensure_dirs()
    applied = configure_environment(settings)

    print("SciNet model provisioning")
    print(f"  store        : {settings.models_dir}")
    print(f"  ollama models: {ollama_dir()}")
    print(f"  hf cache     : {hf_dir()}")
    print(f"  binaries     : {bin_dir()}")
    print(f"  vram budget  : {VRAM_BUDGET_MIB} MiB")
    print(f"  HF_HOME      : {applied['HF_HOME']}")
    print()
    print_manifest()

    entries = [e for e in REGISTRY if not args.role or e.role.value == args.role]

    server = PrivateOllama(settings)
    needs_server = any(e.runtime is Runtime.OLLAMA for e in entries) or args.extra
    if needs_server:
        if server.binary is None:
            print("the `ollama` binary is not installed; cannot provision models")
            return 2
        server.start()
        print(f"private ollama: {server.url}\n")

    print("provisioning:")
    ok = True

    # Tier 1's inference server. Upstream ships no Linux CUDA build, so this is
    # the Vulkan one; it reaches the NVIDIA card through the vendor ICD and
    # needs no CUDA toolkit, no compiler and no root.
    if not args.role or args.role == "ocr":
        if llama_server_path().exists():
            print(f"  [ok]      llama-server already in {bin_dir().name}/")
        elif args.check:
            print("  [missing] llama-server (tier 1 inference backend)")
            ok = False
        else:
            try:
                provision_llamacpp(
                    settings, on_progress=lambda m: print(f"  [bin]     {m}")
                )
            except Exception as exc:  # noqa: BLE001
                print(f"  [fail]    llama-server: {exc}")
                ok = False
    for entry in entries:
        if entry.runtime is Runtime.OLLAMA:
            ok &= ensure_ollama(entry, server, args.check)
        else:
            ok &= ensure_huggingface(entry, args.check)

    for reference in args.extra:
        print(f"  [pull]    {reference} (comparison candidate)")
        server.pull(reference, on_progress=lambda ln: None)

    if args.measure:
        print("\nmeasuring resident footprints (download size does not predict this):")
        results: list[tuple[str, int, int]] = []
        targets = [e.reference for e in entries if e.runtime is Runtime.OLLAMA]
        targets += args.extra
        for reference in targets:
            stub = ModelEntry(
                role=Role.VISION,
                runtime=Runtime.OLLAMA,
                reference=reference,
                quantization="",
                disk_mib=0,
                vram_mib=None,
                purpose="",
            )
            measured = measure(stub, server)
            if measured:
                results.append((reference, *measured))

        print("\n  summary:")
        for reference, total, vram in results:
            fits = vram > 0 and total <= VRAM_BUDGET_MIB
            print(
                f"    {reference:<28} resident={human(total)} vram={human(vram)} "
                f"{'FITS' if fits else 'DOES NOT FIT'}"
            )

        # Persist through the same path the Model Lab uses. This line used to
        # read "update vram_mib in models_registry.py with these numbers" —
        # a loop closed by hand, which is to say usually not closed.
        from app.core.db import session_scope
        from app.services.models.profiles import record_measurement, seed_profiles

        with session_scope() as session:
            seed_profiles(session)
            for reference, total, vram in results:
                record_measurement(session, reference, total_mib=total, vram_mib=vram)
        print("\n  recorded in the model catalog (see the Model Lab, /api/models)")

    print()
    if args.check and not ok:
        print("some models are missing; run without --check to download them")
        return 1
    print("done" if ok else "some models could not be provisioned")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
