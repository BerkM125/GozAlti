# What we can actually build, from what — capability map

Written to answer "what models do we have and what can we DO with them". Organised by
the **signal we want**, not by the model. Every latency here is measured on the GB10
over our own 23 SDOT frames (`lab/bench_detectors.py`), not estimated.

Companion docs: `MODEL-MENU.md` (raw inventory), `ARCHITECTURE.md` (why detector+VLM
split), `lab/MODELS.md` (VLM bench tables).

---

## The two engines

| | detector (torchvision, CUDA, in the vLLM container) | VLM (ollama / vLLM) |
|---|---|---|
| answers | **where / how many / what object** | **what is happening / what does it mean** |
| cost | **60–72 ms per frame** | 3.0–3.6 s per frame |
| 646-camera sweep | **39–47 s** | 32–39 min |
| fails by | missing small/occluded objects | hallucinating, drifting from schema |
| never | reads a sign, understands a closure | counts reliably |

Everything below is a combination of these two, plus a third cheap engine we have not
used yet: **plain arithmetic over the corpus** (ranking, deltas, tracking).

---

## Capability table

Status: ✅ built · 🔬 tested, not wired · ⬜ available, untested · ❌ not possible here

| # | Capability | How | Cost | Status |
|---|---|---|---|---|
| 1 | **People located + counted** | `fasterrcnn_resnet50_fpn_v2` boxes, conf per box, normalized cx/cy | 64 ms | ✅ `lab/detect.py` |
| 2 | **Vehicles counted by type** | same pass — COCO has car/truck/bus/motorcycle/bicycle/train | **free** (same 64 ms) | ✅ |
| 3 | **True silhouette outlines** | `maskrcnn_resnet50_fpn_v2` — pixel mask per person, not a rectangle | 72 ms (**+8 ms**) | 🔬 |
| 4 | **Facing direction / posture** | `keypointrcnn_resnet50_fpn` — 17 body keypoints; shoulder-hip vector = heading, ankle spread = walking vs standing | **60 ms (faster than #1)** | 🔬 |
| 5 | **Busy-vs-quiet, per signal** | percentile rank of each camera against every other camera **in the same sweep** — separates pedestrian streets (p98 people / p9 cars) from arterials (p43 / p98) | free, arithmetic | ✅ `detect.rank()` |
| 6 | **Weather, measured not opined** | COCO `umbrella` count from the same pass; rain confirmed by object, not by a model's guess | free | ⬜ one-line change |
| 7 | **Is this a real sidewalk?** | COCO `bench` / `parking meter` / `fire hydrant` / `traffic light` / `stop sign` — street furniture implies pedestrian infrastructure | free | ⬜ |
| 8 | **Scene reading** — closure, scaffolding, emergency, queue, transit stop, "no sidewalk on this ramp" | VLM `qwen3-vl:8b` on `prompts/insight.txt`, detector counts injected as facts it must not recount | 3.6 s | ✅ `lab/insight.py` |
| 9 | **Street name from the frame** | already emerging from #8 (VLM reads the sign); dedicated OCR via `deepseek-ocr:3b` / `glm-ocr` would be sharper | 3.6 s / ~2 s | ✅ partial, ⬜ OCR |
| 10 | **Camera heading (which way it looks)** | fuse: OCR street names (#9) + keypoint facing distribution (#4) + sun/shadow azimuth from timestamped frames + one-way traffic direction. No dataset has this field — it must be inferred | mixed | ⬜ designed, unbuilt |
| 11 | **Unique people over time (not per-frame)** | centroid/IoU tracker over a video clip → stable ids, dwell time, direction of travel. Per-frame counts undercount a busy corner badly | free on top of #1 | 🔄 in progress (video pipeline) |
| 12 | **Change vs this corner's own normal** | corpus arithmetic: today's count vs this camera's history. Needs the 2,700-read archive from safe-walk | free | ⬜ |
| 13 | **Semantic search over observations** | `Qwen3-Embedding-0.6B` / `qwen3-embedding:8b` — "every camera where a sidewalk is blocked" | ~ms | ⬜ |
| 14 | **Action recognition from clips** | `r3d_18` / `mvit_v2_s` / `swin3d_t` (Kinetics-400) — needs HLS clips, classes are generic ("walking", "crossing") | ~100 ms/clip | ⬜ low value |
| 15 | **NVIDIA-branded reasoning** | `Cosmos-Reason1-7B` — physical-world VLM, **VSS's own default**. On disk, not served | 3–6 s | ⬜ **highest pitch value** |
| 16 | **3D / depth / splat of the street** | — | — | ❌ nothing on disk; single fixed viewpoint can't reconstruct |

---

## Composition — what stacks into a product beat

**Beat 1 — "the whole city, in under a minute."**
#1 + #2 + #5 across 646 cameras = 42 s on one box. Dots on a map, every corner ranked
against every other. No cloud, no API bill. *This is the Spark argument, measured.*

**Beat 2 — "and here's what's actually going on at the one you care about."**
#8 on the ~40 cameras along the user's route = 2.4 min. Produces the sentences a
detector cannot: *"no sidewalk visible on this ramp"*, *"construction cones and a sign
blocking the lane ahead"*. **This is the VLM earning its place**, and it is the See-track
answer.

**Beat 3 — "watch it move."**
#11 over a 60 s clip: unique people, not frame counts; dwell time; direction of travel.
Turns a still into behaviour.

**Beat 4 — the honesty beat.**
#5 + confidence per box + "we never output a safety score". Every number traceable to a
box on a frame or a row in SDOT's collision record.

---

## Correction #1 — the maskrcnn cost I quoted was measured badly

I said "+6 ms" from the faster of two frames, compared against a sweep-wide average.
Re-benched properly (all 23 frames, warmup per config, min of 2 timed passes):

| model | mean | median | p90 | 646-cam sweep | people found |
|---|---|---|---|---|---|
| `fasterrcnn` native | **64.4 ms** | 63.4 | 67.3 | 41.6 s | 133 |
| `maskrcnn` native | **72.2 ms** | 71.1 | 77.0 | 46.6 s | 132 |
| `keypointrcnn` native | **60.1 ms** | 60.7 | 63.8 | **38.9 s** | 121 |

So the real cost of silhouettes is **+7.8 ms/frame, +5.0 s per city sweep** — close to
what I claimed, but I got there by luck, not method. The 168 ms outlier that triggered
the re-check was a warmup artifact, not the HD frame being slow. Keypoints are *cheaper*
than plain boxes (the model is person-only).

## Correction #2 — the bigger one: resolution barely matters, and that's a problem

I said "stop downscaling and we recover distant pedestrians on the HD cameras". Measured:

| model | native | capped at 1024 |
|---|---|---|
| fasterrcnn | 64.4 ms · 133 people | 64.6 ms · 134 people |
| maskrcnn | 72.2 ms · 132 | 70.7 ms · 131 |
| keypointrcnn | 60.1 ms · 121 | 60.1 ms · 115 |

Feeding 1080p costs nothing **and gains nothing** — because torchvision's detection
transform resizes internally to `min_size=800 / max_size=1333` regardless of what we
hand it. Our input resolution has been irrelevant this whole time.

That means the HD third of the camera fleet is currently **wasted**, and there is a real
unexploited win: raise `min_size` (e.g. 1333/1600) or tile the frame, which should
recover the ~10 px pedestrians everything misses today. Costs more compute; needs a
measurement, not a guess. **Untested — do not quote a number for it.**

---

## CV assisting the VLM — measured, and it fixed a wrong answer

`lab/assist.py`. Cheap CV decides **where to look**, then the VLM answers a narrow
question about a large crop instead of a broad question about a small blurry region.

Test case `CMR-0236` (Aurora Ave N & Harrison St), the same frame, three ways:

| method | walkway verdict |
|---|---|
| qwen3-vl on the full frame | `clear` — "construction equipment and barriers on the right side" |
| cosmos-reason1 on the full frame | `clear` — "construction activity visible" |
| **CV-selected crop → qwen3-vl** | **`on_walking_path: true` — "blocks walking path with barriers and equipment"** |

Both full-frame reads **saw** the construction and still called the walkway clear. The
crop shows why they were wrong: a drill rig, a skid-steer, stacked pipe, cones, caution
tape and fencing sitting across the sidewalk. At full-frame scale that is a smear on the
right edge; at 1129x973 it is unmistakable. The frame is tagged `blocked` in our sample
set, so the crop-based answer is the correct one and the full-frame answer was a miss.

Cost: 3 crops, **2.3 s each**, on a frame the detector had already processed.

Two region finders, both cheap:
- **`unmatched`** — detector proposals scoring between the objectness floor (0.10) and the
  labelling threshold (0.45), with confident people excluded. This is literally the
  "something is there and COCO has no word for it" set, and it is where sidewalk
  obstructions live: scaffolding, barricades, cones, sandwich boards, debris, skips.
- **`static`** — background-subtract against the camera's own recent frames. Changed and
  then stopped = obstruction; changed and still moving = traffic. Pure cv2 (5.0 is in the
  container), no GPU, no model. Needs ≥3 history frames from the same camera — we have
  31k archived frames, so this is free.

It also **gates** VLM spend: no regions found, no VLM call at all. That is the opposite
of running a 6 s model on all 646 cameras.

Molmo's role becomes clear here too. Its weakness was counting (prompt-fragile: 1 point
vs 26 on the same frame). Its strength is specifics — served on vLLM it answers "point at
what is blocking the path" with coordinates rather than a paragraph. Ask it *what and
where within a crop*, never *how many across a scene*.

## Design decisions taken (grill session, Sat night)

| decision | choice | why |
|---|---|---|
| what the camera score *is* | the **live term** of safe-walk's existing `static_risk()`, not a new number | the base is SDOT's collision record — a fact a judge can check. Camera evidence moves it within a cap, as `live.py` already does. A standalone camera score is unfalsifiable and breaks the no-invented-scores rule. |
| what earns an expensive model | **anomaly-triggered + route on demand** | detector on all 646 every sweep (42 s); promote to VLM on rank-delta vs last sweep, appear-and-stop CV, or confidence collapse. ~20–60 VLM calls/sweep instead of 646 (68 min, stale before it finishes). |
| who converts evidence → risk | **`vlm` emits facts, `synthesis` owns the weights table, `harness` maps camera→segment** | keeps "the VLM never judges" literally true, puts the weights where teammates can see and tune them, and needs no §6.2 contract change. |
| how much history the trigger uses | **previous sweep only** | the archive is ~10 h from one day, dominated by night; safe-walk's own `baseline.py` refuses to claim "unusual for this hour" from it. Rank-delta + appear-and-stop need one sweep of memory and are honest on day one. |

## Honest gaps

- **No ground truth.** Every comparison is model-vs-model plus eyeballed overlays. ~50
  hand-counted frames would make all of this defensible; nobody has done it.
- **Night with people: 1 frame** in the sample set. Under-tested, and it is the
  scenario the product is pitched on.
- **The ~335 px cameras** (3 of 23) are close to useless for people. Treat vehicle-only.
- **Camera heading (#10) is unsolved** and blocks "the camera can only see this block".
- **VSS itself is not deployed** — we interoperate in shape (ingest → VLM → index →
  reason) and can serve VSS's own VLM (#15), but the blueprint containers are not on the
  box.
