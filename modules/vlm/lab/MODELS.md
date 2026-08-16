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

## Benchmark — 23 sample frames × 3 prompts × 3 models (Sat 15 Aug 18:00, ollama warm, serial)

| model | caption parse · s/frame | boxes parse · s/frame · total | points parse · s/frame · total | count within ±2 of caption |
|---|---|---|---|---|
| `qwen2.5vl:7b` | 23/23 · **3.0 s** | 21/23 · 6.4 s · 52 | 19/23 · 4.7 s · 51 | 20/21 |
| `qwen3-vl:8b` | 23/23 · 3.3 s | **23/23** · 6.3 s · 132 | **22/23 · 3.8 s** · 102 | 22/23 |
| `gemma4:12b` | 23/23 · 4.6 s | 22/23 · 5.9 s · 59 | 22/23 · 7.6 s · 109 | 15/22 |

Per-frame counts (Mac ref = the Mac's own Qwen2.5-VL `people_visible`, not truth):

| frame | mac | q2.5 box | q2.5 pt | q3 box | q3 pt | g4 box | g4 pt |
|---|---|---|---|---|---|---|---|
| crowd CMR-0016 | 10 | 5 | ✗ | 11 | 11 | 1 | 8 |
| crowd CMR-0039 | 10 | 8 | 11 | 11 | 12 | ✗ | 15 |
| crowd CMR-0176 | 15 | 10 | ✗ | 13 | 14 | 20 | 12 |
| crowd CMR-0261 | 10 | ✗ | ✗ | 39† | ✗ | 1 | 30 |
| crowd CMR-0303 | 10 | ✗ | ✗ | 14 | 16 | 9 | 12 |
| crowd CMR-0428 | 10 | 6 | 12 | 11 | 15 | 1 | ✗ |
| few CMR-0055/0163/0170/0174/0306 | 4 each | 3/4/2/4/5 | 4/4/3/7/4 | 4/4/6/6/5 | 5/4/5/7/6 | 4/6/1/7/1 | 4/7/4/6/5 |
| blocked/construction ×4 | 2/1/0/0 | 2/1/0/0 | 2/1/0/1 | 2/2/0/1 | 2/2/0/1 | 1/1/1/1 | 1/1/0/2 |
| empty ×3, night ×3, wet ×2 | 0/0/0 · 0/1/0 · 1/0 | all correct | all correct | 1 false + on CMR-0021 | 1 false + | 1 false + ×2 | 1 false + |

✗ = JSON truncated at the token cap (crowds). † = ~14 real + a ladder of ~25 repeated boxes along a fence (repetition loop).

### Conclusions

1. **Caption schema works on all three** (69/69 parse with `format: json`). qwen2.5vl fastest (3.0 s), gemma4 slowest (4.6 s) and drifts (invents keys, `crowding: none` on 10-person frames).
2. **For dots (`cx`,`cy`) use `qwen3-vl:8b` with the points prompt**: 22/23 parse, **3.8 s/frame**, only 1 truncation, counts track crowds (11–16 where the others cap out). Coordinates: `[x,y]` on a 0–1000 grid → divide by 1000 = the contract's normalised `cx`,`cy` directly.
3. **Boxes are the wrong output shape**: 4 coords/person blows the token cap on crowds (qwen2.5vl ✗ on 2–4 crowd frames at 1500 tok, 35 s). Points halve tokens; still cap at ~30 people.
4. **All models over-count on cluttered frames** — qwen3-vl 39 (ladder), gemma4 30 on CMR-0261. Mitigate: cap `num_predict` (~800), dedupe points closer than ~1.5 % of width, treat >25 as `crowd` flag + capped count.
5. **False positives on empty frames are rare but real** (1 phantom person on CMR-0021 for qwen3/gemma). Confidence isn't exposed by ollama; use agreement (caption count vs points count) as the confidence proxy.
6. **Coordinate conventions differ per family** and silently break overlays: qwen2.5vl = pixels of the sent image (send at 1456 long edge), qwen3-vl = xyxy/1000, gemma = **yxyx/1000** (points `[y,x]`). `ask.py convention()` encodes this — verified on frames, not assumed.
7. Night frames (dark_lit) parsed fine on all three; too few night frames with people (1) to judge night counting.

Recommendation for the endpoint: **qwen3-vl:8b, two calls per frame** (caption schema + points), ~7 s serial on the box today, ~2 s/frame at 4 concurrent — a 200-camera en-route sweep in ~7 min serial, well under 2 min concurrent. Keep qwen2.5vl:7b as the fallback (same prompts; only the coordinate convention changes).
