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
