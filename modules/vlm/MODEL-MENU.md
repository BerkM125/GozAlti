# What we can plug in — models on the box, by job

Everything here is **already on the machine**; nothing needs a download except
torchvision weights (~170–230 MB each, cached in `~/junk/torchcache`). Inventory taken
Sat 15 Aug after copying the SSD kit to `~/models/`.

Ordered by what it does for us, not by what it is.

---

## 1. Counting and locating — the detector half

All of these are in the vLLM container (`torch 2.11 + torchvision 0.26 + CUDA`), run on
the GB10, and are ~300x faster than asking a VLM for coordinates.

| model | what you get | measured | status |
|---|---|---|---|
| **`fasterrcnn_resnet50_fpn_v2`** | boxes + confidence, 91 COCO classes | **66 ms/frame** | **in use** (`lab/detect.py`) |
| **`maskrcnn_resnet50_fpn_v2`** | the above **+ a pixel mask per person** — true silhouette outlines, not rectangles | **72 ms** | tested ✅, not wired |
| **`keypointrcnn_resnet50_fpn`** | 17 body keypoints per person → **facing direction**, standing vs walking, seated | **65 ms** | tested ✅, not wired |
| `retinanet_resnet50_fpn_v2`, `fcos_resnet50_fpn` | alternative box detectors | — | `--arch` flag already supports |
| `ssdlite320_mobilenet_v3`, `fasterrcnn_mobilenet_v3_large_320_fpn` | tiny/fast variants | — | if we ever need 646 cams at 1 Hz |
| `deeplabv3_resnet101`, `fcn_resnet101` | semantic segmentation, 21 VOC classes | — | **no sidewalk/road class** — limited use |
| `r3d_18`, `mvit_v2_s`, `swin3d_t` | action recognition over a clip (Kinetics-400) | — | only useful once media-ingest gives us HLS clips |

### Yes — we have cars, and more
The detector is COCO-91, so **vehicles come free from the same 66 ms pass**:
`car`, `truck`, `bus`, `motorcycle`, `bicycle`, `train` (the light rail downtown).
Already counted and ranked in `detect.py`.

Other COCO classes worth mining from the *same* pass, no extra cost:

| class | why it matters to a walker |
|---|---|
| `traffic light`, `stop sign` | controlled vs uncontrolled crossing |
| `umbrella` | **rain, measured** — independent of any model's weather opinion |
| `bench`, `parking meter`, `fire hydrant` | street furniture → this is a real sidewalk, not a shoulder |
| `backpack`, `handbag`, `suitcase` | luggage → travellers, station/airport corridors |
| `dog` | dog walkers = residential foot traffic |
| `train` | light rail crossing the walking path |

**Next win, cheap:** swap `fasterrcnn` → `maskrcnn` and you get literal outlines around
people (silhouettes) at +6 ms, plus the mask area gives an occlusion/size signal for free.

---

## 2. Understanding the scene — the VLM half

The reasoning layer. Counts get injected; the VLM never recounts.

| model | where | notes |
|---|---|---|
| **`qwen3-vl:8b`** | ollama | **in use** (`lab/insight.py`), 3.6 s/frame, 0 parse fails, reads street signs off the frame |
| `qwen2.5vl:7b` | ollama | fallback, 3.0 s captions, weaker at grounding |
| `gemma4:12b` | ollama | vision **+ audio**; caption-capable, drifts on schema |
| **`Molmo2-8B`** | vLLM :8000, **running now** | Ai2/Seattle. Native pointing. Very prompt-sensitive for counting — but as a *reasoner* it is untested and worth one pass |
| **`Cosmos-Reason1-7B`** | `~/models/vlm/`, **not yet served** | NVIDIA's physical-world reasoning VLM and **VSS's own default VLM**. This is the direct answer to "is it VSS, is it on the Spark". Highest-value untested item. |
| `Qwen2.5-VL-7B`, `Qwen3-VL-8B` (full weights) | `~/models/vlm/` | HF copies for vLLM if we want batching beyond ollama |
| `deepseek-ocr:3b`, `glm-ocr` | ollama | **street-sign OCR** → the camera-heading problem, and reading closure/detour signs verbatim |
| `nemotron-3-nano-omni-30b` + `mmproj` | `~/unsloth/`, llama.cpp | NVIDIA-branded VLM via `llama-server`; 30B-A3B MoE, untested |

---

## 3. Retrieval / search
| model | job |
|---|---|
| `Qwen3-Embedding-0.6B` (`~/models/embed/`), `qwen3-embedding:8b`, `nomic-embed-text` | semantic search over the observation corpus — "show me every camera where a sidewalk is blocked" |

## 4. Text reasoning (synthesis, not this module)
`qwen3:32b`, `gpt-oss:120b`, `qwen3-coder:30b`, `devstral:24b`, `glm-4.7-flash`,
`nemotron-cascade-2:30b`, `qwen3.5:122b`.

## 5. Not for this project
`~/models/{3d,cad,imagegen}` — Hunyuan3D, TRELLIS, TripoSG, CAD-Recode, FLUX. Object-level
mesh/image generation from the earlier hackathon kit. No scene-reconstruction model exists
on disk (no splat/NeRF/depth/pose) — see `experiments/safe-walk/docs/feedback-nvidia-*.md`.

---

## Correction: camera resolution is NOT uniform

Earlier note said SDOT frames are 720x480. Measured across our 23 samples:

| resolution | frames |
|---|---|
| **1920x1080** | 8 |
| 720x480 | 10 |
| 1280x720 | 2 |
| 335x244 / 335x209 | 3 |

**A third of the cameras are full HD.** That changes things:
- distant pedestrians are recoverable on the 1080p cameras — the detector should get
  their native resolution, not a downscale
- the ~335 px cameras are close to useless for people; treat them as vehicle-only
- worth carrying resolution per camera and weighting confidence by it

---

## Recommended next moves, in order

1. **`maskrcnn` swap** — true outlines, +6 ms. Directly what the frontend wants to draw.
2. **`Cosmos-Reason1-7B` on vLLM** — the VSS answer for judges; one bench pass against
   `qwen3-vl:8b` on `prompts/insight.txt`.
3. **`keypointrcnn`** — facing direction per person; feeds both the camera-heading problem
   and "waiting at the curb" vs "walking".
4. **OCR pass on street signs** — camera heading from the frame itself.
5. Mine the extra COCO classes (`umbrella`, `traffic light`, `bench`) — free signals from
   a pass we already run.
