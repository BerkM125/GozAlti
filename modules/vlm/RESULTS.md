# Results — Saturday night session

Everything measured on the Acer GN100 (GB10). Two work threads ran in parallel and are
consolidated here; a background agent built the video pipeline and viewer, this thread
built the CV-assist path, the VSS bench and the guards. Both are merged into `main`.

---

## 1. Video pipeline — the VSS shape, on real Seattle footage

Four live HLS clips pulled from SDOT cameras, 60 s each, sampled at 4 fps = **240 frames
per clip, 960 frames total**. Each clip: detector on every frame → centroid/IoU tracking →
10 s chunks → VLM caption per chunk → whole-clip digest.

| camera | location | unique people | peak/frame | mean/frame | vehicles mean |
|---|---|---|---|---|---|
| CMR-0428 | 5th Ave S & S Jackson St | **154** | 25 | 16.83 | 3.21 |
| CMR-0303 | 2nd Ave & Pike St | **95** | 19 | 12.56 | 3.90 |
| CMR-0176 | 5th Ave & Pine St | **64** | 11 | 3.73 | 9.17 |
| CMR-0261 | Alaskan Way & Wall St | **12** | 4 | 0.83 | 14.33 |

### The headline finding: stills undercount people by up to 6x

`CMR-0428` peaks at **25 people in any single frame** but **154 distinct people pass
through in 60 seconds**. A snapshot-based system sees 25 and calls that the answer. It is
off by a factor of six, and it is wrong in the direction that matters — a corner that
looks moderately busy in a still is heavily trafficked on foot.

This is the argument for video, measured on our own cameras rather than asserted.

### The still-frame ranking held up

The 23-frame still bench ranked `CMR-0428` at p98 pedestrians / **p9** vehicles, and
`CMR-0261` at p87 / **p93**. The video agrees independently: 428 runs 154 people against
3.2 vehicles/frame; 261 runs 12 people against 14.3. Two different methods, same
conclusion — 428 is a pedestrian street, 261 is an arterial. That is the first thing all
night that validated rather than corrected an earlier claim.

### What the VLM added over the counts

Per-chunk captions on `CMR-0176` tracked a bus through the clip across four consecutive
chunks — arriving, stopping, departing — and flagged `transit_stop` on each. It also
described what *changed* within each chunk ("a blue bus enters on the right and a white
SUV replaces the dark sedan"). No detector output contains that; it is the summarization
half of Video Search and Summarization doing its job.

The clips are night footage (pulled ~21:00), and the VLM read the lighting correctly
throughout — "nighttime urban intersection with decorative tree lights". Night with
people was our thinnest test case on stills (1 frame); this adds 960 night frames.

Artifacts per clip in `lab/video_out/<camera>/`: `annotated.mp4`, `frames/`, `strips/`,
`digest.txt`. All gitignored — rebuild with `./video.py`.

---

## 2. CV assists the VLM — and corrected a wrong answer

`lab/assist.py`. Cheap CV picks the region, the VLM answers a narrow question about a
large crop. Full detail in `CAPABILITIES.md`; the short version:

On `CMR-0236`, both qwen3-vl **and** cosmos-reason1 called the walkway `clear` on the full
frame while flagging construction. The CV-selected crop (1129x973, 2.3 s) returned
`on_walking_path: true` — "blocks walking path with barriers and equipment". The crop
image confirms it: drill rig, skid-steer, stacked pipe, cones, caution tape and fencing
across the sidewalk. Our sample set tags that frame `blocked`, so the crop was right and
both full-frame reads were misses.

**Attention routing beat a bigger model.** Cosmos at 11.3 s got it wrong; a 2.3 s crop got
it right.

---

## 3. VSS positioning — we benched the blueprint's own VLM and it lost

`Cosmos-Reason1-7B` served on vLLM on the Spark, against `qwen3-vl:8b`, same prompt, same
23 frames:

| | cosmos-reason1 | qwen3-vl:8b |
|---|---|---|
| mean latency | 11.33 s | **6.31 s** |
| safety verdicts asserted | **3/23** ✗ | **0/23** ✓ |
| `no_sidewalk` found | 2 | **3** |
| `construction` found | 3 | **4** |

Cosmos called `I5UnionRev`, a reversible freeway lane, `clear`. qwen3-vl correctly said
`no_sidewalk`. Full write-up and the sentence to say on stage in `VSS-PLAN.md`.

---

## 4. Guards and infrastructure

- **`verdict_check.py`** — mechanical gate. `prompts/insight.txt` already said "never
  judge safety" and Cosmos said "allowing pedestrians to cross safely" anyway on 13% of
  frames. Prompt text does not hold; this fails the build instead.
- **`status.py`** — live ops dashboard on `:8090` (GPU, served models, running jobs,
  per-model record counts with verdict status inline, newest overlays). Stdlib, no CDN,
  works with the wifi down.
- **`viewer/` + `serve_viewer.sh`** — offline results viewer for judges.
- Three infrastructure fixes that were silently breaking things: `TORCH_HOME` moved off
  `/root` (uid 1000 cannot write there), `/etc/passwd` mounted into the container
  (torch calls `getpwuid()`), and ollama `num_ctx` capped at 16k — the 262144 default was
  reserving **44.5 GB** of KV cache and starving the detector on a shared 121 GB box.

---

## 4b. GROUND TRUTH — and the full-frame VLM fails the product's core claim

78 frames hand-labelled blind (no model output shown, no filename tags). Only walkway
status; the rubric is in `lab/label.py`. Scored with `lab/score_labels.py`.

### The eval set (55 frames): zero obstructions

| clear | no_sidewalk | unclear | blocked | narrowed |
|---|---|---|---|---|
| 43 (78%) | 7 (13%) | 5 (9%) | **0** | **0** |

Miss rate is not measurable from this set. Two lessons: sidewalk obstruction is **rare**
in a random downtown sample, and 7 of the 7 `no_sidewalk` labels were WSDOT freeway
cameras — knowable from the city's `OWNERSHIP` field without looking at a pixel. Filter
`OWNERSHIP = SDOT` (387 of 658) before building any future eval set.

### The samples set (23 frames): the key was wrong, not the models

First pass scored 3 obstructions that both models called `clear`, which looked like a
100% miss rate on the product's core claim. The labeller then flagged their own work:
*"technically sidewalks were there but I flagged as obstruction simply because there was
construction and I assumed so."*

Checked every disputed frame by eye. **All three labels were wrong and both models were
right:**

| frame | labelled | what the image actually shows | correct |
|---|---|---|---|
| `CMR-0236` Aurora & Harrison | blocked | drill rig, skid-steer, pipe, cones and caution tape staged in the **curb lane of the roadway**, behind fencing | clear |
| `CMR-0262` 4th S & Holgate | narrowed | striped barricade and orange sign in a **traffic lane**; sidewalk and crosswalk right of frame unobstructed | clear |
| `CMR-0185` 2nd & Battery | blocked | fenced demolition site on the **far** side; the near sidewalk is clear and has a pedestrian walking on it | clear |

Both models: 0 false alarms across 23 frames, and correct on all three of these. The
honest read is that we have **no confirmed obstruction anywhere in 78 labelled frames**,
which is consistent with the eval set's 0/55 and with obstruction simply being rare.

### The crop path produced FALSE POSITIVES — retraction

An earlier section of this document (and `CAPABILITIES.md`) claimed the CV-crop path
"corrected a wrong full-frame answer" on `CMR-0236`. **That claim is withdrawn.** The
crop returned "construction equipment and barriers, blocks walking path"; the full frame
shows the equipment is in the roadway. The crop was wrong and the full frame was right.
Re-run on all three disputed frames, the crop path asserted `on_walking_path: true`
twice — **two false positives against the full frame's zero**.

The mechanism matters more than the score:

> **Cropping removes the context required to judge "is it on the path."** Close up,
> construction equipment looks like an obstruction. Zoomed out you can see it is staged
> in a traffic lane behind a fence. Attention routing helps a model *see* small things;
> it actively hurts a judgement that depends on the spatial relationship between the
> thing and the sidewalk.

So `assist.py` should be used to answer "what is this object", never "does it block the
path". The on/off-path judgement needs the wide frame.

### What this episode actually establishes

1. **Construction is not obstruction.** Both the human labeller and the crop path made
   the same error — seeing construction and inferring a blocked path. The full-frame
   models did not. This is a labelling-guide problem as much as a model problem; the
   rubric now says it, but it said it only after the labels were made.
2. **Verify before publishing.** Two findings in this document were wrong because a
   plausible result was written up without checking the source image. Both are corrected
   above rather than deleted, because the error pattern is the lesson.
3. **We still have zero confirmed obstructions.** Nothing here measures miss rate. The
   honest position for the demo is that obstruction detection is **unvalidated**, and
   saying so is stronger than quoting a number built on assumptions.

## 5. Corrections made tonight

Kept here because the wrong versions were reported before they were checked.

| claim | corrected to |
|---|---|
| "maskrcnn costs +6 ms" (from the faster of two frames, vs a sweep average) | **+7.8 ms** mean over all 23 frames; keypointrcnn is *cheaper* than plain boxes at 60.1 ms |
| "feed 1080p to recover distant pedestrians" | **no effect** — torchvision resizes to `min_size=800` internally. 64.4 vs 64.6 ms, 133 vs 134 people. Raising `min_size` is the real knob |
| "SDOT frames are 720x480" | 8 of 23 samples are **1920x1080**, 3 are ~335 px |
| "3D/splat is not possible" | too flat — a digital twin from media-ingest's 3D building data + keypoint facing is feasible post-hackathon |
| "the VLM should count people" | it should not; the detector is ~300x faster and more accurate. The VLM reads the situation |

## 6. Still open

- **No ground truth.** Every accuracy claim is model-vs-model or eyeballed. 40 hand-labelled
  frames for obstruction-only would settle it; a labelling tool is not built.
- **`walkway_status` conflates two questions** — see `SPEC.md`. Existence should come from
  SDOT's sidewalk inventory (3,148 downtown segments, already joined in `graph.py`);
  the VLM should answer obstruction only.
- **Camera heading unsolved**, which blocks "this camera can only see this block".
- **Risk scoring not implemented** — design decided (`CAPABILITIES.md`), needs `synthesis`
  to own the flags→delta weights table.
