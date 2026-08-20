"""Project-owned model storage and a private model server.

SciNet does not use the machine's system-wide Ollama models. It keeps its own
weights under ``SCINET_MODELS_DIR`` and, when it needs to serve them, starts its
own ``ollama`` process pointed at that directory on its own port. The result is
a checkout that carries its models with it: nothing depends on what someone
pulled globally six months ago, and nothing this project downloads pollutes the
user's home directory.

    data/models/
      ollama/   OLLAMA_MODELS for the private server (GGUF blobs + manifests)
      hf/       HF_HOME for torch/transformers weights (Marker, embeddings)

Two caveats worth stating plainly:

* The ``ollama`` *binary* is still required. That is a much weaker dependency
  than the models, and the alternative — compiling llama-cpp-python against
  CUDA — trades a 20 MB binary for a build toolchain.
* ``HF_HOME`` only takes effect if it is set before ``transformers`` or
  ``torch`` import their cache module, which is why ``configure_environment``
  is called from process entrypoints rather than lazily.
"""

from __future__ import annotations

import logging
import os
import shutil
import socket
import subprocess
import time
from contextlib import closing
from pathlib import Path

import httpx

from app.core.config import Settings, get_settings

logger = logging.getLogger(__name__)

#: Deliberately not 11434: the private server must never collide with, or be
#: mistaken for, the user's system-wide Ollama.
DEFAULT_PRIVATE_PORT = 11500
STARTUP_TIMEOUT_SECONDS = 60


def ollama_dir(settings: Settings | None = None) -> Path:
    return (settings or get_settings()).models_dir / "ollama"


def hf_dir(settings: Settings | None = None) -> Path:
    return (settings or get_settings()).models_dir / "hf"


def bin_dir(settings: Settings | None = None) -> Path:
    """Project-local executables (currently llama.cpp's server for tier 1)."""
    return (settings or get_settings()).models_dir / "bin"


def llama_server_path(settings: Settings | None = None) -> Path:
    return bin_dir(settings) / "llama-server"


def provision_llamacpp(settings: Settings | None = None, on_progress=None) -> Path:
    """Download llama.cpp's server into the project, if it is not there already.

    Tier 1 (Marker + Surya 2) needs an external inference server. Upstream
    publishes no Linux CUDA build, so the Vulkan one is used: it reaches the
    NVIDIA GPU through the vendor ICD, needs neither the CUDA toolkit nor a
    compiler, and unpacks to roughly 32 MB inside ``data/models/bin``. Nothing
    is installed system-wide and nothing requires root.
    """
    import io
    import tarfile

    settings = settings or get_settings()
    target = llama_server_path(settings)
    if target.exists():
        return target

    from app.core.models_registry import LLAMACPP_ASSET, LLAMACPP_URL

    destination = bin_dir(settings)
    destination.mkdir(parents=True, exist_ok=True)

    if on_progress:
        on_progress(f"downloading {LLAMACPP_ASSET}")

    with httpx.stream(
        "GET", LLAMACPP_URL, follow_redirects=True, timeout=600.0
    ) as response:
        response.raise_for_status()
        payload = io.BytesIO()
        for chunk in response.iter_bytes(1024 * 256):
            payload.write(chunk)
    payload.seek(0)

    # The archive is flat under a single llama-<build>/ directory. Flatten it,
    # and carry the symlinks: every shared library ships as a real file plus a
    # chain of version aliases (libllama-common.so -> .so.0 -> .so.0.1.2), and
    # the loader resolves the *alias*, so dropping them leaves a binary that
    # cannot start.
    with tarfile.open(fileobj=payload, mode="r:gz") as archive:
        members = archive.getmembers()
        for member in members:
            name = Path(member.name).name
            if not name or member.isdir():
                continue
            out = destination / name

            if member.issym():
                if out.is_symlink() or out.exists():
                    out.unlink()
                out.symlink_to(member.linkname)
                continue

            if not member.isfile():
                continue
            extracted = archive.extractfile(member)
            if extracted is None:
                continue
            out.write_bytes(extracted.read())
            if not name.startswith("lib"):
                out.chmod(0o755)

    if not target.exists():
        raise RuntimeError(f"{LLAMACPP_ASSET} did not contain llama-server")
    if on_progress:
        on_progress(f"installed {target}")
    return target


def configure_environment(settings: Settings | None = None) -> dict[str, str]:
    """Point every model-downloading library at the project's own cache.

    Must run before torch/transformers are imported. Returns what it set, so
    callers can log it and users can see where their disk is going.
    """
    settings = settings or get_settings()
    cache = hf_dir(settings)
    cache.mkdir(parents=True, exist_ok=True)

    from app.core.models_registry import LLAMACPP_DEVICE, LLAMACPP_GPU_LAYERS

    device = settings.llamacpp_device or LLAMACPP_DEVICE

    applied = {
        "HF_HOME": str(cache),
        "HF_HUB_CACHE": str(cache / "hub"),
        "TRANSFORMERS_CACHE": str(cache / "hub"),
        "SENTENCE_TRANSFORMERS_HOME": str(cache),
        # Surya keeps its own cache root, separate from the HF hub layout.
        "MODEL_CACHE_DIR": str(cache / "surya"),
        "TORCH_HOME": str(cache / "torch"),
        # Tier 1: force the local llama.cpp server. Left to autodetect, Surya
        # sees an NVIDIA GPU and tries to spawn a vllm Docker container, which
        # needs the nvidia container runtime and root to install.
        "SURYA_INFERENCE_BACKEND": "llamacpp",
        "LLAMA_CPP_BINARY": str(llama_server_path(settings)),
        "LLAMA_CPP_NGL": str(LLAMACPP_GPU_LAYERS),
        # Without this llama.cpp takes Vulkan0, which is the Intel iGPU on this
        # machine. See LLAMACPP_DEVICE in models_registry for the enumeration.
        "LLAMA_CPP_EXTRA_ARGS": f"--device {device}",
        # Surya constrains layout output with an OpenAI json_schema, which
        # llama-server compiles to a GBNF grammar. That schema contains a
        # bounded-repetition regex (^\d{1,4} \d{1,4} \d{1,4} \d{1,4}$) which
        # this build's converter rejects: every layout call returns
        # "Failed to initialize samplers: failed to parse grammar", Marker logs
        # "Layout inference failed; leaving page empty", and the page silently
        # degrades to unstructured text at ~16x tier-0 cost. Turning the
        # constraint off lets the model emit JSON unguided, which it was trained
        # to do — headings and reading order come back, and warm throughput goes
        # from ~4.7 s/page to under 1 s/page. Revisit if llama.cpp's schema
        # converter learns bounded repetition.
        "SURYA_GUIDED_LAYOUT": "false",
        # Surya's default inference timeout is 600 s per call. A degenerate page
        # — one whose glyphs all extract as "?", say — makes the VLM generate
        # until it exhausts its token budget, and a single such page was
        # measured at 546 s. On a 4,000-paper backfill that is an unbounded
        # stall on unbounded input. Failing fast is strictly better: the router
        # treats a tier-1 error as "fall through", so the page still gets read.
        "SURYA_INFERENCE_TIMEOUT_SECONDS": str(settings.tier1_timeout_seconds),
        # Cap the per-page layout budget for the same reason.
        "SURYA_MAX_TOKENS_LAYOUT": str(settings.tier1_max_layout_tokens),
    }
    os.environ.update(applied)
    return applied


def _port_is_open(host: str, port: int) -> bool:
    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as sock:
        sock.settimeout(0.5)
        return sock.connect_ex((host, port)) == 0


class PrivateOllama:
    """A project-scoped ``ollama serve`` reading only from the project's store."""

    def __init__(self, settings: Settings | None = None, port: int | None = None):
        self.settings = settings or get_settings()
        self.port = port or self.settings.ollama_port
        self.host = "127.0.0.1"
        self._process: subprocess.Popen | None = None

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.port}"

    @property
    def binary(self) -> str | None:
        return shutil.which("ollama")

    def environment(self) -> dict[str, str]:
        store = ollama_dir(self.settings)
        store.mkdir(parents=True, exist_ok=True)
        return {
            **os.environ,
            "OLLAMA_MODELS": str(store),
            "OLLAMA_HOST": f"{self.host}:{self.port}",
            # One model resident at a time: on an 8 GiB card, a second load
            # evicts the first anyway, and thrashing is worse than waiting.
            "OLLAMA_MAX_LOADED_MODELS": "1",
            "OLLAMA_NUM_PARALLEL": "1",
            "OLLAMA_KEEP_ALIVE": self.settings.ollama_keep_alive,
        }

    def is_running(self) -> bool:
        return _port_is_open(self.host, self.port)

    def start(self) -> None:
        """Start the private server, unless one is already listening."""
        if self.is_running():
            logger.debug("private ollama already listening on %s", self.url)
            return
        if self.binary is None:
            raise RuntimeError(
                "the `ollama` binary is not installed; see README for setup"
            )

        logger.info(
            "starting private ollama on %s with models from %s",
            self.url,
            ollama_dir(self.settings),
        )
        self._process = subprocess.Popen(
            [self.binary, "serve"],
            env=self.environment(),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )

        deadline = time.monotonic() + STARTUP_TIMEOUT_SECONDS
        while time.monotonic() < deadline:
            if self.is_running():
                return
            if self._process.poll() is not None:
                raise RuntimeError("private ollama exited during startup")
            time.sleep(0.4)
        raise TimeoutError(f"private ollama did not come up on {self.url}")

    def stop(self) -> None:
        if self._process is None:
            return
        self._process.terminate()
        try:
            self._process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self._process.kill()
        self._process = None

    # --- model management ------------------------------------------------

    def installed(self) -> set[str]:
        try:
            response = httpx.get(f"{self.url}/api/tags", timeout=10.0)
            response.raise_for_status()
        except Exception:  # noqa: BLE001 - a down server means "nothing installed"
            return set()
        return {m.get("name", "") for m in response.json().get("models", [])}

    def pull(self, reference: str, on_progress=None) -> None:
        """Download a model into the project's store."""
        if self.binary is None:
            raise RuntimeError("the `ollama` binary is not installed")

        process = subprocess.Popen(
            [self.binary, "pull", reference],
            env=self.environment(),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        assert process.stdout is not None
        for line in process.stdout:
            if on_progress:
                on_progress(line.rstrip())
        if process.wait() != 0:
            raise RuntimeError(f"failed to pull {reference}")

    def resident_footprint_mib(self, reference: str) -> tuple[int, int]:
        """(total_mib, vram_mib) for a currently-loaded model.

        ``vram_mib`` of 0 with a non-zero total is the signature of a model that
        did not fit and is being served from system RAM.
        """
        response = httpx.get(f"{self.url}/api/ps", timeout=10.0)
        response.raise_for_status()
        for model in response.json().get("models", []):
            if model.get("name") == reference:
                return (
                    int(model.get("size", 0)) // (1024 * 1024),
                    int(model.get("size_vram", 0)) // (1024 * 1024),
                )
        return (0, 0)

    def unload(self, reference: str) -> None:
        try:
            httpx.post(
                f"{self.url}/api/generate",
                json={"model": reference, "keep_alive": 0},
                timeout=30.0,
            )
        except Exception:  # noqa: BLE001
            logger.warning("could not unload %s", reference)


PRIVATE_OLLAMA = PrivateOllama()
