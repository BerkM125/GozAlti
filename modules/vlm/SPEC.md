# modules/vlm — VLM Step (model's output)

**Owner:** Adi · **Endpoints on the Spark box:** Dhruv · **Effort budget:** 4 h

Read `../../SPEC.md` first. Lane: this directory only.

## Scope

Vision analysis of camera footage, running on the Spark (GB10) and exposed as an
HTTP endpoint on **:8040**:

- **VSS-first**: the See track expects NVIDIA's Video Search and Summarization
  (VSS) Blueprint — deploy it (or its VLM component) on the box and build our
  analysis on top. Nemotron Lightning / Meta Glimmer are on-site hard drives if
  a different backbone is needed. Judges will ask "is it on the Spark, is it
  VSS" — the answer must be yes.
- Consume `FrameRecord` (§6.1) from media-ingest; emit `Observation` (§6.2):
  people count, detections with normalized `cx`/`cy` (this is what the frontend
  draws as dots), closed-enum `flags`, short caption.
- Own the flag enum. Start from: `blocked_sidewalk`, `poor_lighting`, `crowd`,
  `no_people`, `construction`, `vehicle_on_sidewalk`, `camera_dead`. Extending
  it is fine (it's yours); document each addition here — synthesis maps flags to
  evidence text.
- **Prompt + schema**: harvest `experiments/safe-walk/safewalk/vision.py` — it
  already has a strict JSON schema, a retry-on-schema-miss loop, and the
  critical guardrail: **the VLM must not output danger/crime verdicts**, only
  observable facts. Keep that guardrail verbatim in spirit.
- Use the sample images in `experiments/safe-walk/data` layout and surukamera's
  `cache/snapshots/` as a prompt-tuning corpus — iterate on real SDOT frames
  (night, rain, dead cameras), not stock photos.

**Out of scope:** fetching imagery (media-ingest), deciding safety (synthesis),
storing history beyond a small result cache.

## Interface

`POST :8040/read` body = `FrameRecord` → `Observation`. Batch endpoint optional.
Target: fast enough that a full en-route camera sweep completes inside
media-ingest's cadence — measure and publish your per-frame latency here.

## Definition of done (demo)

A frame with pedestrians returns accurate detections that render as dots in the
frontend; a dead-camera placeholder returns `camera_dead` and no hallucinated
people; sustained throughput survives the live demo sweep.

## Practices

- Validate output against the `Observation` schema server-side; retry the model
  on miss, never hand malformed JSON downstream.
- Keep a `samples/` dir of hard frames + expected outputs as a mini eval; run it
  after every prompt change.
- Never hallucinate: prefer `no_people`/low confidence over invented detections.

## Quickstart

Edit on the Mac, `git push`, then on the box `cd ~/GozAlti && git pull`. Never edit
on the box. GPU work runs in the vLLM container (torch + torchvision + CUDA + cv2 5.0
already inside); everything else is stdlib.

```bash
# on the box, from modules/vlm/lab/
./run_box.sh detect  'samples/*.jpg'          # detector: boxes, vehicles, per-sweep ranks   ~64 ms/frame
./insight.py         'samples/*.jpg'          # VLM reads the situation, counts injected     ~6.3 s/frame
./assist.py FRAME --mode unmatched            # CV picks a region -> crop -> VLM close-up     ~2.3 s/crop
./video.py CLIP.mp4                           # VSS shape: chunk -> caption -> summarize -> Q&A
./verdict_check.py insight_*.jsonl            # gate: fails if the VLM asserted safety/danger
./status.py --port 8090                       # live processing dashboard
./serve_viewer.sh                             # offline results viewer for judges
```

| file | what |
|---|---|
| `detlib.py` | shared detector: load / infer / parse / score / rank / draw |
| `detect.py` | detector over stills; `--arch fasterrcnn\|maskrcnn\|keypointrcnn`, `--min-size` |
| `insight.py` | VLM scene reading, detector counts injected as facts it must not recount |
| `assist.py` | CV region finders (`static` background-subtract, `unmatched` proposals) → crop → VLM |
| `video.py`, `track.py` | clip pipeline + centroid/IoU tracking for unique-people counts |
| `verdict_check.py` | mechanical guard: no safety/danger/judgement language ships |
| `bench_detectors.py` | same protocol across detectors, split by resolution bucket |
| `status.py`, `viewer/` | live ops dashboard; offline results viewer |
| `prompts/` | `insight` (scene), `scene`, `caption`, `people`/`points` (grounding), `chunk_caption`, `clip_summary`, `clip_qa` |
| `samples/` | 23 real SDOT frames across crowd/blocked/construction/night/wet/empty, `mac_reads.json` as reference |

Findings live in `../ARCHITECTURE.md` (why detector+VLM), `../CAPABILITIES.md` (what each
model buys), `../VSS-PLAN.md` (VSS positioning + the Cosmos bench), `MODELS.md` and
`NOTES.md` (raw numbers). Artifacts (`*.jsonl`, `*_out/`, `video_out/`) are gitignored.

## Known design issue: `walkway_status` conflates two questions

The enum is `clear | narrowed | blocked | no_sidewalk | unclear`, which mixes:

- **does a sidewalk exist here** — a permanent property of the street, and
  **SDOT already publishes it**: `experiments/safe-walk/data/static/sidewalks.geojson`
  carries 3,148 downtown segments with `sidewalk_ratio` and `sidewalk_condition`, already
  joined onto the street graph in `graph.py`.
- **is it passable right now** — transient, and the only part a camera can answer.

So the VLM currently spends ~6 s/frame re-deriving a fact the city already published, and
gets it wrong sometimes: Cosmos-Reason1 called `I5UnionRev`, a reversible freeway lane,
`clear`. Saying "clear" about a freeway segment is the worst single error this system can
make to a pedestrian.

**Fix**: take existence from the SDOT inventory, and narrow the VLM's question to
obstruction only — `clear | narrowed | blocked | unclear`, plus what is doing the
obstructing. Deferred, not done; it needs a `SegmentAssessment` conversation with
synthesis since existence would then arrive from the static layer instead of ours.
