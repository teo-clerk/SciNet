"""The models SciNet requires, declared once.

SciNet ships its own models rather than using whatever happens to be installed
system-wide. A globally installed model may be absent, a different
quantization, or silently updated, and none of that should decide whether this
project works. Everything named here is provisioned into ``SCINET_MODELS_DIR``
by ``scripts/download_models.py`` and served from there.

Two numbers are recorded per model and they are not interchangeable:

``disk_mib``
    Size of the weights on disk. Cheap to verify, useless for capacity planning.
``vram_mib``
    *Measured* resident footprint while serving. This is what has to fit.

The gap between them is the whole reason this file exists. ``qwen2.5vl:7b`` is
5.56 GiB on disk but was measured at 13.3 GiB resident — its vision tower and
KV cache more than double it — so on an 8 GiB card Ollama silently ran it
entirely on CPU at 181 s/page instead of erroring. Never infer ``vram_mib``
from a parameter count or a download size; measure it with
``scripts/download_models.py --measure``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

# Usable VRAM on the target card, less headroom for the display server and
# fragmentation. A model above this budget will be pushed to CPU by Ollama.
TOTAL_VRAM_MIB = 8188
VRAM_SAFETY_MARGIN_MIB = 512
VRAM_BUDGET_MIB = TOTAL_VRAM_MIB - VRAM_SAFETY_MARGIN_MIB

#: Models measured on this hardware and rejected, kept so the reasoning is not
#: lost and nobody re-adopts them on the strength of their download size.
#: reference -> (disk_mib, measured_resident_mib, why)
#: llama.cpp release used for tier 1's inference server, pinned for
#: reproducibility. Upstream ships **no prebuilt Linux CUDA binary** — the CUDA
#: assets are Windows-only — so the Vulkan build is used instead. It reaches the
#: NVIDIA GPU through the vendor's Vulkan ICD, needs no CUDA toolkit and no
#: compilation, and costs about 32 MB. Building llama.cpp from source against
#: CUDA would be modestly faster and is the upgrade path if tier 1 ever becomes
#: the bottleneck.
LLAMACPP_RELEASE = "b10502"
LLAMACPP_ASSET = f"llama-{LLAMACPP_RELEASE}-bin-ubuntu-vulkan-x64.tar.gz"
LLAMACPP_URL = (
    "https://github.com/ggml-org/llama.cpp/releases/download/"
    f"{LLAMACPP_RELEASE}/{LLAMACPP_ASSET}"
)

#: Layers offloaded to the GPU by llama-server. Surya's default of 99 means
#: "all", which is right when the model fits and an OOM when it does not.
LLAMACPP_GPU_LAYERS = 99

#: Which Vulkan device llama.cpp should use. This machine is hybrid-graphics
#: and enumerates the Intel iGPU *first*:
#:
#:     Vulkan0: Intel Arc (MTL)              23808 MiB (shared system RAM)
#:     Vulkan1: NVIDIA RTX 4060 Laptop        8188 MiB
#:
#: llama.cpp defaults to device 0, so leaving this unset silently runs tier 1
#: on the integrated GPU. The iGPU advertises far more "VRAM" because it is
#: shared system memory, which makes the wrong choice look like the safe one.
LLAMACPP_DEVICE = "Vulkan1"

REJECTED_MODELS: dict[str, tuple[int, int, str]] = {
    "qwen2.5vl:7b": (5693, 13619, "CPU-only on 8 GiB; measured 181 s/page"),
    "qwen2.5vl:3b": (3052, 10291, "CPU-only on 8 GiB despite a 2.98 GiB download"),
}


class Runtime(StrEnum):
    #: Served by the project-scoped Ollama instance, weights under models/ollama.
    OLLAMA = "ollama"
    #: Loaded in-process by torch/transformers, weights under models/hf.
    HUGGINGFACE = "huggingface"


class Role(StrEnum):
    OCR = "ocr"  # tier 1 layout parsing
    VISION = "vision"  # tier 2 page transcription
    EMBED = "embed"  # document and chunk vectors
    TAG = "tag"  # summaries, tags, cluster names


@dataclass(frozen=True)
class ModelEntry:
    role: Role
    runtime: Runtime
    reference: str
    quantization: str
    disk_mib: int
    #: Measured resident footprint. ``None`` means "not yet measured on this
    #: hardware" — preflight treats that as unproven rather than as fitting.
    vram_mib: int | None
    purpose: str
    required: bool = True
    notes: str = ""
    alternatives: tuple[str, ...] = field(default_factory=tuple)

    @property
    def key(self) -> str:
        return f"{self.role.value}:{self.reference}"

    @property
    def fits_vram(self) -> bool | None:
        if self.vram_mib is None:
            return None
        return self.vram_mib <= VRAM_BUDGET_MIB


# --- the manifest ---------------------------------------------------------
#
# Tier 2's model is deliberately a small one. It is the last resort for the ~1%
# of pages nothing else can read, so its job is to fit alongside everything
# else and finish, not to be the best vision model available.

REGISTRY: tuple[ModelEntry, ...] = (
    ModelEntry(
        role=Role.OCR,
        runtime=Runtime.HUGGINGFACE,
        reference="datalab-to/surya-ocr-2-gguf",
        quantization="GGUF + fp16 mmproj",
        disk_mib=2200,
        vram_mib=None,
        purpose="Tier 1: layout-aware PDF to Markdown (Marker + Surya 2).",
        notes=(
            "Surya 2 is itself a VLM and does not run in-process. It needs an "
            "external server: either a vllm Docker container requiring the "
            "nvidia container runtime, or llama.cpp's llama-server. SciNet uses "
            "the latter, provisioned into data/models/bin, so tier 1 stays "
            "self-contained and needs no Docker and no root."
        ),
        alternatives=("marker-pdf<2 (in-process torch)", "docling (MIT)"),
    ),
    ModelEntry(
        role=Role.VISION,
        runtime=Runtime.OLLAMA,
        reference="granite3.2-vision:2b",
        quantization="GGUF Q4_K_M + fp16 projector",
        disk_mib=2324,
        vram_mib=3605,  # measured: 3.52 GiB, 100% resident on GPU
        purpose="Tier 2: transcribe pages neither the text layer nor Marker can read.",
        notes=(
            "Chosen by measurement, not by parameter count. Both Qwen2.5-VL "
            "builds were rejected: the 7B is 13.3 GiB resident and the 3B is "
            "10.05 GiB, so both are served from system RAM on an 8 GiB card at "
            "roughly 181 s/page. That family's dynamic-resolution vision tower "
            "carries a ~3.4x activation budget over its download size. Granite "
            "is 2.27 GiB on disk, 3.52 GiB resident, and fully GPU-resident; "
            "IBM also trained it specifically on documents, charts and tables."
        ),
        alternatives=("minicpm-v:8b", "llava:7b", "moondream"),
    ),
    ModelEntry(
        role=Role.EMBED,
        runtime=Runtime.HUGGINGFACE,
        reference="malteos/scincl",
        quantization="fp32 safetensors",
        disk_mib=420,
        vram_mib=768,
        purpose="Document and chunk vectors; 768-dim, drives the map and search.",
        notes=(
            "Chosen by measurement against a corpus whose true partition is "
            "known (300 arXiv papers filed under 10 fields). SciNCL is a "
            "SciBERT trained with neighbourhood contrastive learning on the "
            "citation graph — papers that cite each other are pulled together — "
            "which is exactly the similarity a map of a paper library needs.\n"
            "\n"
            "Measured, same corpus and clustering, ARI / homogeneity / "
            "completeness / noise:\n"
            "  malteos/scincl        0.672 / 0.762 / 0.825 / 0\n"
            "  allenai/specter2_base 0.582 / 0.767 / 0.709 / 14\n"
            "  Qwen3-Embedding-0.6B  0.561 / 0.700 / 0.773 / 9\n"
            "\n"
            "SPECTER2's marginally higher homogeneity is the over-fragmentation "
            "trap: it split 10 fields into 13 clusters and discarded 14 papers, "
            "and purity is trivially bought that way. Completeness catches it.\n"
            "\n"
            "Note the 512-token limit. Document text is title + abstract + "
            "summary + headings, median ~455 tokens, so most papers fit and the "
            "rest truncate into exactly the title-and-abstract prefix this "
            "model family was trained on."
        ),
        alternatives=(
            "allenai/specter2_base",
            "Qwen/Qwen3-Embedding-0.6B",
            "BAAI/bge-m3",
        ),
    ),
    ModelEntry(
        role=Role.TAG,
        runtime=Runtime.OLLAMA,
        reference="qwen3:8b",
        quantization="GGUF Q4_K_M",
        disk_mib=5325,
        vram_mib=5673,  # measured: 5.54 GiB, 100% resident on GPU
        purpose="Summaries, controlled-vocabulary tags, and cluster names.",
        notes="Runs alone: nothing else may be resident while tagging.",
        alternatives=("qwen3:4b", "gemma3:4b"),
    ),
)


def by_role(role: Role) -> ModelEntry:
    for entry in REGISTRY:
        if entry.role is role:
            return entry
    raise KeyError(f"no model registered for role {role}")


def required_models() -> tuple[ModelEntry, ...]:
    return tuple(e for e in REGISTRY if e.required)


def total_disk_mib() -> int:
    return sum(e.disk_mib for e in required_models())


def ollama_models() -> tuple[ModelEntry, ...]:
    return tuple(e for e in REGISTRY if e.runtime is Runtime.OLLAMA)


def huggingface_models() -> tuple[ModelEntry, ...]:
    return tuple(e for e in REGISTRY if e.runtime is Runtime.HUGGINGFACE)
