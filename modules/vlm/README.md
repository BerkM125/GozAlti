# modules/vlm — start here

Reads a Seattle traffic-camera frame and returns structured facts about it: where the
people are, how many vehicles, how lit the scene is, what is happening, and a short
caption. Runs on the GB10 box. Nine documents grew out of one weekend; this is the door.

**One sentence:** a detector measures, a VLM describes, and neither is ever allowed to
say whether a place is safe.

---

## Run it

```bash
ssh acer01@gn100-3511.local
cd ~/GozAlti/modules/vlm
./run.sh install     # one-time, idempotent: container + GPU check, weights, warm ollama
./run.sh start       # service on :8040
./run.sh test        # 33 tests: unit tier then live tier against the running service
./run.sh status | logs | restart | stop
```

Open **`http://gn100-3511.local:8040/`** (or `http://100.106.143.38:8040/` on the
tailnet) for a zero-dependency demo page that exercises every endpoint and renders the
result, including the evidence chain behind each field.

**Code is never edited on the box.** Edit on the Mac → `git push` → on the box
`git pull && ./run.sh restart`. Anything install-shaped lives in `run.sh`.

## The contract

`POST /read` takes a `FrameRecord` (SPEC §6.1) and returns an `Observation` (§6.2):

```json
{"camera_id":"CMR-0039","frame_ts":"…","read_at":"…","model":"torchvision/fasterrcnn+qwen3-vl:8b",
 "people_count":15,"detections":[{"label":"person","cx":0.71,"cy":0.80,"conf":0.996}],
 "flags":["construction"],"caption":"Pedestrians crossing at 4th Ave & Olive Way…",
 "_ext":{"vehicles":{"car":3,"bicycle":3,"train":1},"illumination":{"mean_luma":84.8,…},
         "scene":"intersection","walkway_status":"clear","detector_ms":66,"cached":false}}
```

Also `POST /read_batch`, `GET /health`, `GET /flags`, `GET /cache`, `DELETE /cache`.
Responses are validated against §6.2 before they leave; on a schema miss the VLM is
retried once, then the observation degrades to detector-only rather than shipping
malformed JSON. A frame media-ingest marks `stale` never reaches a model.

## Architecture in one table

| stage | does | cost |
|---|---|---|
| **detector** (torchvision Faster R-CNN, CUDA) | people boxes + confidence + normalized cx/cy, vehicles by COCO class, per-sweep percentile ranks | **66 ms** |
| **illumination** (cv2 histogram) | mean luma, dark fraction, spread | **~1 ms** |
| **VLM** (qwen3-vl:8b via ollama) | scene type, walkway status, events, street names read off signs, caption | **~2.4 s** |
| **cache** (frame-keyed LRU) | identical frame → identical answer | **1 ms** |

Detector does the counting because it is ~300× faster and more accurate than asking a
VLM for coordinates. The VLM does what nothing else can: read a closure sign, notice
scaffolding, recognise a freeway ramp with no sidewalk. Full reasoning in
`ARCHITECTURE.md`.

## Measured numbers (all on the GB10, none estimated)

| | |
|---|---|
| `/read` cold | ~2.5 s |
| `/read_batch` at concurrency 2 | 2.49 s/frame (ollama saturates at 2) |
| cached read | **1 ms** |
| detector alone, 646 cameras | **42 s** |
| three users, same 6-camera route | 14.6 s, then 0.00 s, then 0.00 s |
| video: unique people vs per-frame peak, 5th & Jackson | **154 vs 25** — stills undercount ~6× |

## What is proven vs what is not

**Proven.** Detector counts and locations. Vehicle counts. Per-sweep ranking (separates
pedestrian streets from arterials; confirmed independently by video). Unique-people
tracking over clips. Zero false alarms from the VLM across 23 labelled frames. Zero
schema failures in service traffic.

**Not proven, and labelled as such in the code.**
- **Obstruction detection is unvalidated.** 78 hand-labelled frames contain **zero**
  confirmed obstructions, so miss rate has never been measured. `blocked_sidewalk` and
  `narrowed_sidewalk` carry this caveat in `SPEC.md`.
- **Illumination thresholds are uncalibrated.** All 35 test frames bucket `lit`, including
  ones shot at 21:47 local, because SDOT cameras auto-expose. Raw numbers are facts; the
  bucket is provisional.
- **No ground truth anywhere else.** Every other accuracy claim is model-vs-model or
  eyeballed.

`RESULTS.md` §4b documents two findings that were published and then retracted after
checking the source images. Read it before trusting anything here.

## Where this sits

`media-ingest :8030` ✅ → **`vlm :8040` ✅** → `synthesis :8020` ✗ (SPEC only) →
`map-frontend` (running on mocks). `harness` is also SPEC only. Scoring belongs to
synthesis by SPEC §5; this module emits facts and never a score.

---

## Good spike targets

Ranked by value per hour. Each says what "done" looks like so a spike can be timeboxed.

1. **Find real obstructions.** Everything downstream is unvalidated because we have no
   positives. Watch the ~27 cameras historically flagged for construction over several
   hours, keep frames where the walking path is genuinely blocked, hand-label with
   `lab/label.py`. *Done:* ≥10 confirmed positives and a measured miss rate from
   `lab/score_labels.py`.

2. **Rank illumination within a sweep instead of by threshold.** Auto-exposure defeats
   absolute luma. Same fix already used for vehicle counts. *Done:* a camera's lighting
   percentile against the rest of the sweep, and `poor_lighting` firing on frames a human
   agrees are badly lit.

3. **Raise the detector's `min_size`.** torchvision resizes to 800 px internally, so the
   HD third of the fleet is currently wasted and ~10 px pedestrians are invisible to
   everything. `detect.py --min-size` already exists. *Done:* a recall/latency curve
   showing whether distant pedestrians are recoverable and at what cost.

4. **Neighbour-camera corroboration.** One camera reporting something odd is noise; three
   adjacent agreeing is signal — and it is exactly what would have caught our crop-path
   false positives. Needs `harness` adjacency. *Done:* a flag that only fires when
   corroborated, measured against the single-camera version.

5. **Swap in Mask R-CNN for true silhouettes.** +7.8 ms measured, gives pixel masks
   instead of rectangles. *Done:* the frontend drawing outlines rather than boxes.

6. **Keypoints for facing direction.** `keypointrcnn` is *cheaper* than the detector we
   run (60 ms vs 66 ms) and gives 17 body points → heading and standing-vs-walking. Feeds
   the unsolved camera-heading problem. *Done:* per-person heading in `_ext`.

7. **The LLM synthesis layer.** VSS is ingest → VLM captions → index → **LLM reasons**.
   We built the first three. `gpt-oss:120b` and `nemotron-cascade-2:30b` sit idle on the
   box. This is synthesis's lane — coordinate before building.

Do **not** spike on classifying the people in frame (behaviour, posture, tent counts).
`SAFETY-SIGNALS.md` §5 explains why, with the practical reasons before the ethical ones.

## Map of the other documents

| file | for |
|---|---|
| `SPEC.md` | the contract, endpoints, flag enum, caveats |
| `ARCHITECTURE.md` | why detector + VLM, with the numbers that forced it |
| `CAPABILITIES.md` | every signal we could extract, what it costs, what is built |
| `RESULTS.md` | what was measured, **and what was retracted** |
| `VSS-PLAN.md` | See-track positioning; the Cosmos-Reason1 bench (it lost) |
| `SAFETY-SIGNALS.md` | CPTED / prospect-refuge design for future signals; the line we do not cross |
| `MODEL-MENU.md` | every model on the box, by job |
| `lab/` | the research workbench the service was built from |
| `lab/evidence/` | artifacts that cannot be regenerated |
