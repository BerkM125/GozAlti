# Putting VSS back at the centre

The See track says teams "work with NVIDIA's VSS (Video Search and Summarization)
skills", and `SPEC.md` §2 says the VLM module "must use or credibly interoperate with
the VSS Blueprint on the Spark". A judge on this track will ask, directly, whether this
is VSS and whether it is on the Spark.

Earlier work in this module drifted: a COCO detector is ~300x faster than a VLM at
counting, so counting moved to the detector and the VLM was left captioning. That is a
correct engineering call and a **bad submission shape** — it makes the mandated component
optional. This document fixes the framing and lists what we build.

## What VSS actually is

The blueprint is not "a VLM that counts". It is a pipeline:

```
ingest video ─> chunk it ─> VLM dense-captions each chunk ─> index (CA-RAG: vector + graph)
                                                              └─> summarize / Q&A / alerts
```
VLM: **Cosmos-Reason1** by default. LLM: Nemotron. Served as NIMs. Two deployment modes
on a single Spark: *Event Reviewer* (fully local VLM pipeline) and *Standard* (hybrid).

Read that shape against what we have and the overlap is nearly total — we built the same
pipeline for 646 still feeds before knowing the blueprint's internals.

## Our three levels of VSS claim, weakest to strongest

| level | claim | cost | status |
|---|---|---|---|
| 1 | *Shape parity* — same pipeline, tuned for 646 still feeds instead of a few video streams | 0 (already true) | ✅ |
| 2 | **Run VSS's own VLM** — `Cosmos-Reason1-7B` on the Spark via vLLM, driving our reasoning pass | ~15 min | 🔄 **serving now** |
| 3 | Deploy the blueprint containers themselves (NGC key, docker compose, large pulls) | 3–4 h, network-dependent | ⬜ stretch only |

Level 2 is the honest, defensible answer and it is nearly free: Cosmos-Reason1 is already
on the box (`~/models/vlm/Cosmos-Reason1-7B`), it is a `Qwen2_5_VLForConditionalGeneration`
so vLLM serves it natively, and our `insight.py` talks OpenAI-compatible already — the
model swaps behind one flag.

**Say on stage:** "The reasoning runs on Cosmos-Reason1, the same VLM the VSS blueprint
uses, served on the Spark. We didn't deploy the blueprint's containers — we built its
pipeline shape for 646 still cameras, which is not what the blueprint targets."
That sentence survives grilling. "We deployed VSS" would not.

## Where the VLM becomes load-bearing (not garnish)

The detector answers *where* and *how many*. Those are inputs. Every **output the user
actually reads** comes from the VLM:

| user-facing thing | who produces it |
|---|---|
| "the near sidewalk is closed, pedestrians are being routed into the street" | **VLM only** |
| "no sidewalk visible on this ramp" — a routing fact | **VLM only** |
| which cross-streets this camera is looking at (read off the sign) | **VLM only** |
| "busiest it has been in the last hour, and here's why" | **VLM** over the index |
| answering a question about the city in words | **VLM only** |
| dots on the map | detector |
| counts and ranks | detector + arithmetic |

Delete the detector and we lose precision and speed. **Delete the VLM and there is no
product** — just dots. That is the framing, and it is true, not spin.

## RESULT: we ran the bench, and VSS's own VLM lost

`Cosmos-Reason1-7B` served on vLLM on the Spark, versus `qwen3-vl:8b` on ollama.
Identical prompt (`prompts/insight.txt`), identical 23 frames, detector counts injected
for both.

| | cosmos-reason1 (VSS default) | qwen3-vl:8b |
|---|---|---|
| mean latency | 11.33 s | **6.31 s** |
| p90 | 12.47 s | **8.31 s** |
| JSON parse | 23/23 | 23/23 |
| **safety verdicts asserted** | **3/23 (13%)** ✗ | **0/23** ✓ |
| `no_sidewalk` found | 2 | **3** |
| `construction` found | 3 | **4** |
| walkway agreement | 22/23 — the one disagreement (`I5UnionRev`, a reversible freeway lane) qwen3-vl called `no_sidewalk` and Cosmos called `clear`; qwen3-vl is right | |

Cosmos is 1.8x slower, finds fewer hazards, misses a no-sidewalk freeway segment, and
breaks our hard "never assert safety" rule on 13% of frames — *"allowing pedestrians to
cross safely"*, despite the prompt explicitly forbidding it (`lab/verdict_check.py`
catches this mechanically because prompt text did not hold).

Its prose reasoning is genuinely richer — it explains *why* a ramp has no sidewalk rather
than just flagging it. That is real, and worth revisiting if the verdict problem is
promptable away. It is not worth shipping today.

**Decision: `qwen3-vl:8b` is the production reasoning model.** Cosmos stays served for
the demo as the VSS artifact and the comparison.

**What to say on stage** — this is stronger than "we used VSS's model":
> "We served Cosmos-Reason1, the VLM the VSS blueprint uses, on the Spark, and benched it
> against Qwen3-VL on our own frames. Cosmos was 1.8x slower, missed a no-sidewalk
> segment, and asserted pedestrian safety on 13% of frames — which our product rules
> forbid. So we ship Qwen3-VL. Here are the numbers."

Reproduce: `lab/insight.py 'samples/*.jpg' -m cosmos-reason1 --api openai` vs
`-m qwen3-vl:8b`, then `lab/verdict_check.py insight_*.jsonl`.

## What we build, in order

1. ~~Cosmos-Reason1 served and benched~~ ✅ **done — see result above.**
2. **Chunk → caption → index → summarize** over real video clips (in progress). VSS's
   literal shape: 60 s clips from 4 downtown cameras, chunked, VLM-captioned per chunk,
   then a whole-clip summary synthesized from the chunk captions.
3. **Search and Q&A over the index.** The "S" in VSS. Natural-language questions answered
   from stored captions + detector timeline, with evidence and an explicit "not visible in
   frame" when the evidence isn't there.
4. **Alerts.** The blueprint's third output: a flag fires when the VLM's reading changes
   materially for a camera on the user's route (walkway becomes blocked, closure appears).

## Honest boundaries — say these before a judge finds them

- We are **not** running the VSS blueprint containers. Shape parity + its VLM, not its
  binaries.
- Our feeds are mostly **still snapshots** (SDOT refresh), not continuous video. HLS
  streams exist for SDOT cameras and we process real clips from them — but the 646-camera
  sweep is stills.
- No ground truth on any of our accuracy numbers; comparisons are model-vs-model plus
  eyeballed overlays.
- The VLM never issues a safety verdict. That is a product rule, not a limitation.

## 3D / splat — deferred, not dismissed

The user's read is right that this is possible; my earlier "not possible" was too flat.
What is genuinely available, for **after** this weekend:

- **Not** splatting a single fixed camera — one viewpoint cannot reconstruct a scene, and
  we hold no splat/NeRF/depth weights on disk.
- **But**: `modules/media-ingest` now carries satellite + **3D building data** (§6.9). A
  city 3D model plus camera pose plus detected people is a renderable scene — people
  placed into a 3D street, which is the "render the people" idea. That is a *digital twin*
  built from geometry we already have, not a reconstruction from pixels.
- Body **keypoints** (`keypointrcnn`, 60 ms — cheaper than plain boxes) give per-person
  facing direction and posture, which is exactly what a renderer needs to place a figure.
- Monocular depth (Depth Anything) would give per-person distance; not on disk, needs a
  download.
- Multi-view is available in principle where downtown camera fields of view overlap, and
  from Street View — both are post-hackathon work.

Order if we pick this up: camera heading (unsolved, blocks everything) → keypoint facing →
place people on the 3D building model → depth refinement.
