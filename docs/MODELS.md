# SciNet model manifest

SciNet ships its own models. It does **not** use whatever is installed in your
system-wide Ollama or your `~/.cache/huggingface`. A globally installed model
may be absent, a different quantization, or silently updated by something else
on the machine, and none of that should decide whether this project works.

Everything below is provisioned into `SCINET_MODELS_DIR` (default
`data/models/`) by `scripts/download_models.py`, and the declarative source of
truth is [`backend/app/core/models_registry.py`](../backend/app/core/models_registry.py).

```
data/models/
  ollama/   private model store, served by SciNet's own Ollama on port 11500
  hf/       HF_HOME for torch/transformers weights (Marker/Surya, embeddings)
```

## Why two numbers per model

**Download size does not predict resident footprint, and the gap is where the
bugs live.**

`qwen2.5vl:7b` is 5.56 GiB on disk. Measured resident footprint on this
machine: **13.3 GiB** — the vision tower and KV cache more than double it. On an
8 GiB card, Ollama does not refuse to load it. It silently serves it from system
RAM, and a page that should take ~10 s takes **181 s**. There is no error
message; a backfill simply appears to hang.

So the registry records a *measured* `vram_mib`, an unmeasured model is treated
as **unproven** rather than assumed to fit, and `GpuSlot.fits()` returns False
for anything it has not seen a measurement for.

Re-measure at any time:

```bash
cd backend && uv run python ../scripts/download_models.py --measure
```

## Hardware budget

| | |
|---|---|
| GPU | NVIDIA RTX 4060 Laptop |
| VRAM reported by `nvidia-smi` | 8188 MiB |
| VRAM visible to torch | **7834 MiB** |
| Budget used for admission control | 7676 MiB (8188 − 512 headroom) |
| Models resident at once | **exactly one** |

The single-resident rule is not tuning; no two of the large models fit
together. The worker therefore drains an entire pipeline stage before switching
models, because a reload costs 5–15 s and thrashing them costs more than the
work itself.

## Required models

| Role | Model | Runtime | Quantization | Disk | Purpose |
|---|---|---|---|---|---|
| `ocr` | `datalab-to/surya-ocr-2-gguf` | llama.cpp (project-local) | GGUF + fp16 mmproj | ~2.2 GiB | Tier 1: layout-aware PDF → Markdown |
| `vision` | `granite3.2-vision:2b` | Ollama (private) | GGUF Q4_K_M + fp16 projector | 2.27 GiB | Tier 2: transcribe pages nothing else can read |
| `embed` | `Qwen/Qwen3-Embedding-0.6B` | HuggingFace / torch | fp16 safetensors | ~1.2 GiB | 1024-dim document + chunk vectors |
| `tag` | `qwen3:8b` | Ollama (private) | GGUF Q4_K_M | 5.20 GiB | Summaries, controlled-vocabulary tags, cluster names |

Total download: **~8.7 GiB** excluding tier 1.

## Tier 1 runs on a project-local llama.cpp

`marker-pdf` 2.0 changed architecture. Surya 2 is itself a VLM and does **not**
run in-process; it needs an external inference server, and it picks one
automatically:

- **`vllm`** — spawns a Docker container (`vllm/vllm-openai`) requiring the
  `nvidia` container runtime. Auto-selected whenever an NVIDIA GPU is present,
  which is why the first benchmark attempt failed with
  `unknown or invalid runtime name: nvidia`.
- **`llamacpp`** — spawns the upstream `llama-server` binary. Chosen here, and
  forced via `SURYA_INFERENCE_BACKEND=llamacpp` so autodetection cannot pull us
  back to Docker.

### Why the Vulkan build

Upstream publishes **no prebuilt Linux CUDA binary** — the CUDA release assets
are Windows-only. The Vulkan build reaches the NVIDIA card through the vendor's
Vulkan ICD, needs neither the CUDA toolkit nor a compiler, and unpacks to about
32 MB:

```
data/models/bin/
  llama-server              pinned to release b10502
  libggml*.so, libllama*.so, libmtmd*.so  (+ their version symlinks)
```

Nothing is installed system-wide and nothing requires root. Building llama.cpp
from source against CUDA would be modestly faster and is the upgrade path if
tier 1 ever becomes the bottleneck; `nvcc` is present at `/opt/cuda/bin/nvcc`.

### Measured tier-1 throughput

With the fixes below, on the pinned discrete GPU:

| fixture | pages | ms/page | note |
|---|---|---|---|
| `two_column.pdf` | 2 | **896** | healthy text layer |
| `mojibake.pdf` | 2 | **598** | healthy layout, broken encoding |
| `with_identifiers.pdf` | 1 | 2774 | headings correctly recovered |
| `scanned_no_text_layer.pdf` | 1 | 23473 | full-page raster |
| `unmapped_glyphs.pdf` | 1 | **546019** | degenerate input — see the guard |

On healthy PDFs tier 1 costs roughly **0.6–0.9 s/page**, against 0.3 s/page for
tier 0 and 8.9 s/page for tier 2. The fixture median (2.9 s/page) is not
representative: five of the six fixtures are deliberately pathological.

### Two fixes tier 1 needs

**1. Guided layout must be off.** Surya constrains layout output with an OpenAI
`json_schema`, which `llama-server` compiles to a GBNF grammar. That schema
contains a bounded-repetition regex (`^\d{1,4} \d{1,4} \d{1,4} \d{1,4}$`)
which this build's converter rejects. Every layout call then returns
`Failed to initialize samplers: failed to parse grammar`, Marker logs
`Layout inference failed; leaving page empty`, and the page **silently**
degrades to unstructured text — at roughly sixteen times tier-0 cost for a
worse result. `SURYA_GUIDED_LAYOUT=false` lets the model emit JSON unguided,
which it was trained to do: headings and reading order return, and warm
throughput drops from ~4.7 s/page to under 1 s/page.

**2. A per-call timeout.** Surya defaults to 600 s per inference call. A
degenerate page — one whose glyphs all extract as `?` — makes the VLM generate
until it exhausts its token budget; a single such page measured **546 seconds**.
`SCINET_TIER1_TIMEOUT_SECONDS` (default 90) makes it fail fast instead, and the
router treats a tier-1 failure as "fall through", so the page still gets read by
tier 2.

### The device trap

This machine is hybrid-graphics, and llama.cpp enumerates the integrated GPU
first:

```
Vulkan0: Intel Arc (MTL)           23808 MiB   <- shared system RAM
Vulkan1: NVIDIA RTX 4060 Laptop     8188 MiB
```

llama.cpp defaults to device 0. Left unset, tier 1 silently runs on the Intel
iGPU — and because the iGPU advertises far more "VRAM" (it is shared system
memory), the wrong choice looks like the safe one. `SCINET_LLAMACPP_DEVICE`
pins it to `Vulkan1`.

## Tier-2 vision: candidates considered

Tier 2 is the last resort for the ~1% of pages neither the text layer nor
Marker can read. Its job is to **fit alongside everything else and finish**, not
to be the best vision model available. Sizes below are real Ollama registry
manifest sizes.

Measured on the RTX 4060 Laptop by loading each model and reading back
`size_vram` from the private server.

| Model | Disk | **Measured resident** | On GPU | Verdict |
|---|---|---|---|---|
| **`granite3.2-vision:2b`** | 2.27 GiB | **3.52 GiB** | **100%** | **Selected.** Fits with room to spare; measured at **8.9 s/page** on a scanned page at 95% GPU utilisation. IBM trained it on documents, charts and tables. |
| ~~`qwen2.5vl:3b`~~ | 2.98 GiB | **10.05 GiB** | **0%** | **Rejected.** A "3B" model that does not fit. |
| ~~`qwen2.5vl:7b`~~ | 5.56 GiB | **13.3 GiB** | **0%** | **Rejected.** Measured at 181 s/page on CPU. |
| `minicpm-v:8b` | 5.10 GiB | not measured | — | Best OCR reputation of the group, but the closest to the ceiling. Measure before adopting. |
| `llava:7b` | 4.41 GiB | not measured | — | Older; weaker on dense text. |
| `moondream` | 1.62 GiB | not measured | — | Fits trivially, but likely too weak for dense scientific pages. |

### The lesson from the Qwen2.5-VL family

Both Qwen2.5-VL builds were rejected, and the 3B is the instructive one: it is
**2.98 GiB on disk and 10.05 GiB resident**, a 3.4x multiplier. Its
dynamic-resolution vision tower allocates activation memory far beyond the
weights. Had the registry trusted the download size — or the "3B" in the name —
SciNet would have shipped a second silently-CPU-only model.

This is why `vram_mib` is only ever written from a measurement, and why
`GpuSlot.fits()` returns `False` for any model it has not seen measured.

Switching is a one-line change plus a pull:

```bash
# .env
SCINET_VLM_MODEL=granite3.2-vision:2b
```
```bash
cd backend && uv run python ../scripts/download_models.py --role vision --measure
```

## Verifying before a long job

```bash
cd backend && uv run python ../scripts/doctor.py
```

Reports detected VRAM, which models are provisioned, and — critically — which
would be served from RAM. Run it before any large backfill; the failure it
catches costs hours and reports nothing on its own.

## Deliberate dependency

The `ollama` **binary** is still required, though none of its models are. That
is a far weaker dependency than the weights, and the alternative — compiling
`llama-cpp-python` against CUDA and hand-wiring vision projectors — trades a
20 MB binary for a build toolchain. If full binary independence is ever needed,
`app/core/model_store.py` is the only file that would change.
