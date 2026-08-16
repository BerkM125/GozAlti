# Models on the Spark box (Acer GN100) — what the VLM step can use

Inventory taken Sat 15 Aug ~17:50 on the box (Ubuntu 24.04 aarch64, GB10, 121 GB
unified, ollama 0.32.13, CUDA 13, llama.cpp built, `vllm/vllm-openai` docker image
present). Broader Mac + SSD inventory: `experiments/safe-walk/docs/models.md`.

## Vision-capable — candidates for reading frames

| Model | Runtime | Grounding (coords) | Status |
|---|---|---|---|
| **`qwen2.5vl:7b`** | ollama, resident | yes — absolute pixels; send frames at long-edge **1456** so coords == image | benchmarked ✅ (below) |
| **`qwen3-vl:8b`** | ollama, resident | yes — 0..1000 grid (`ask.py` rescales) | benchmarked ✅ |
| `gemma4:12b` | ollama (vision+audio) | not reliable | benchmarked, caption only |
| `gemma3:12b` | ollama (vision) | no | not run |
| `Nemotron-3-Nano-Omni-30B-A3B` (Q4_K_XL GGUF + `mmproj-BF16.gguf`) | `~/unsloth/…`, serve with `~/llama.cpp/build/bin/llama-server` | unknown | **NVIDIA-branded VLM on the Spark** — worth one run for the judges' question; reasoning model → slower |
| `deepseek-ocr:3b`, `glm-ocr` | ollama | n/a | OCR — street-name plates / signs in frame (camera-heading problem, not people) |
| `medgemma:4b` | ollama | no | irrelevant |
| `Llama-3.2-11B-Vision-Instruct` | HF cache stub only (12 KB — weights not downloaded) | no | skip |

## Not vision (text/agent) — for synthesis / osint, not this module
`qwen3:32b`, `qwen3-coder:30b`, `devstral:24b`, `gpt-oss:120b/20b`, `qwen3.5:122b/9b/4b/0.8b`,
`glm-4.7-flash`, `nemotron-cascade-2:30b` (text, thinking), `deepseek-r1:*`, `llama3.1`,
`mistral-nemo`, `gemma3:12b`(vision but weak), plus GGUFs `Qwen3.6-35B-A3B`, `gemma-4-26B-A4B`,
`Llama-3.3-70B-NVFP4` (needs vLLM/TRT). Embeddings: `nomic-embed-text`, `qwen3-embedding:8b`.

## Absent on the box (as of now)
- **NVIDIA VSS blueprint** (no containers, no NIMs) — SPEC wants "VSS or credible interop".
- Cosmos-Reason1-7B, Molmo2 — on the team SSD (`HACKATHON_MODELS/vlm/`), not copied yet.
- "Nemotron Lightning" / "Meta Glimmer" (SPEC §2 says on-site drives) — nowhere on disk.

## Runtimes available
- **ollama** (:11434) — what `ask.py` uses. Note ollama sizes contexts generously
  (`qwen3-vl:8b` resident at 45 GB / 262k ctx); fine with 121 GB, but three VLMs resident at once
  is ~70 GB.
- **llama.cpp** `llama-server` / `llama-mtmd-cli` (CUDA, GB10 detected) — for the GGUF VLMs (Nemotron Omni).
- **vLLM** docker (`vllm/vllm-openai:latest`, 30 GB, aarch64) — for HF safetensors models
  (Cosmos-Reason1, Molmo2, Qwen2.5-VL full) if we copy weights over; OpenAI-compatible endpoint.

## What data we can extract per frame (and which model gives it)

| Signal | Source prompt | Model | Notes |
|---|---|---|---|
| lighting / weather_surface / traffic / crowding | `prompts/caption.txt` (production schema) | qwen2.5vl, qwen3-vl, gemma4 | closed enums; JSON mode → 100 % parse so far |
| `sidewalk_blocked`, `construction`, `emergency_activity` | caption | same | → `flags` (`blocked_sidewalk`, `construction`, …) |
| `people_visible` (a number) | caption | same | qwen2.5vl rounds to 10 on crowds — do not trust for >5 |
| **per-person boxes → `cx`,`cy` dots** | `prompts/people.txt` | qwen2.5vl (@1456), qwen3-vl | honest counts; ~1 token per box × 4 coords → crowds need `-n 1500` |
| `notable` free text → `caption` | caption | same | short; never a verdict |
| dead camera (`camera_dead`) | none needed | — | placeholder JPEG hash, from media-ingest (`stale`); do not spend a VLM call |
| street signs / text | OCR prompt | deepseek-ocr / glm-ocr | side quest: camera heading |

## Benchmark — 23 sample frames, both prompts

_Filled in from `bench/` (see NOTES.md for method). Numbers are wall seconds per call, ollama warm._

(pending — see below once bench completes)
