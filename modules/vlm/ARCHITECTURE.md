# modules/vlm — how the two halves split, and why

Decided Sat 15 Aug from measurements on our own 23 SDOT frames (not from priors).
Numbers below are reproducible: `lab/detect.py`, `lab/insight.py`, `lab/MODELS.md`.

## The question that forced this

We started by asking a VLM to count people and emit their coordinates. That is the
wrong tool, and our own bench says so:

| | VLM emitting coordinates | COCO detector |
|---|---|---|
| per frame | **8–27 s** (qwen3-vl, molmo2, qwen2.5vl) | **66 ms** |
| 646-camera sweep | 1.4–4.8 hours | **42 s** |
| people found, CMR-0039 | 12 | **15** (incl. 2 on the far sidewalk the VLM missed) |
| false positives | boxed a traffic signal | none on that frame |
| confidence per detection | none exposed | yes, per box |
| output reliability | 1–4 frames/23 truncate on crowds | deterministic |
| prompt sensitivity | molmo: 1 point vs 26 on the same frame, different wording | none |

A detector is ~300x faster, more accurate, gives calibrated confidence, and cannot
hallucinate a person that isn't there. Counting is a solved problem and we should not
be re-solving it with a language model.

## The split

```
frame ──> detect.py  (torchvision Faster R-CNN v2, CUDA, 66 ms)
          │  WHERE and HOW MANY
          │  people boxes + conf + normalized cx/cy   -> frontend dots/outlines
          │  vehicles by type (car/truck/bus/moto/bike/train)
          │  per-sweep percentile ranks               -> pedestrian / traffic / population
          ▼
       counts injected as given facts
          ▼
      insight.py  (VLM, 3.6 s)
             WHAT IS HAPPENING
             walkway clear/narrowed/blocked/no_sidewalk + why
             events: construction, road_closure, emergency_response, crowd,
                     queue, loading, stalled_vehicle, transit_stop
             scene type + setting notes (reads street signs, spots bus shelters,
             tram tracks, tunnel mouths, ramps)
             -> flags for synthesis, evidence text for the user
```

The VLM is told the detector's numbers and explicitly told **not to recount**. It spends
its tokens on the thing nothing else in the stack can do.

## Why this is the stronger VLM story, not a weaker one

The See track wants perception-first work built with VSS-style skills. VSS itself is not
"a VLM that counts" — it is a pipeline: ingest → VLM dense captions → index → reason.
Our shape is the same, tuned for 646 *still* feeds instead of video streams. And the
demo beat gets better, not worse:

- Detector: "every camera in the city, 42 seconds, here are the dots."
- VLM: *"the near sidewalk on 4th is closed and pedestrians are being routed into the
  street"* — a sentence no detector can produce and the exact thing a walker needs.

Measured examples from one 5-frame run, all from the VLM, none available from counting:

| camera | what the VLM added |
|---|---|
| I5CherryRamp | `walkway: no_sidewalk` — "WSDOT Cherry Street Ramp, no sidewalk visible" |
| CMR-0262 | "construction cones and a sign blocking the lane ahead" |
| CMR-0428 | read the cross-streets off the sign; noted bus shelter + tram tracks |
| CMR-0236 | bus-only lane, construction equipment and barriers on the right |

`no_sidewalk` on a freeway ramp is a routing fact. That is the whole product.

## Scores (descriptive, never a verdict)

- `people_count`, `vehicle_count`, `population = people + vehicles` — raw counts.
- `visibility_proxy` — mean detector confidence. When a view is dark, wet or smeared the
  detector's own confidence drops. It says *trust these counts this much*; it is a
  measurement, not an opinion.
- `pedestrian_rank` / `traffic_rank` / `population_rank` — percentile against every other
  camera **in the same sweep**. Absolute thresholds are meaningless here: a freeway ramp
  with 16 cars is empty, a downtown block with 16 is jammed, because the fields of view
  differ. Ranking within a sweep cancels sun, weather and day-of-week; what is left is
  this corner versus the rest of the city right now. (Same reasoning as
  `experiments/safe-walk` `baseline.rank_now`.)

Worked example, same sweep: `CMR-0428` is p98 for people and p9 for vehicles (a
pedestrian street); `CMR-0021` is p43 people and p98 vehicles (an arterial). One number
could never have said that.

No score in this module means "safe" or "unsafe". Synthesis combines evidence; the VLM
describes; the detector counts.

## Cost of a full sweep on the GB10

| stage | per frame | 646 cameras |
|---|---|---|
| detector | 66 ms | **43 s** |
| VLM on every camera | 3.6 s | 39 min |
| VLM on flagged/en-route cameras only (~40) | 3.6 s | **2.4 min** |

So: detector everywhere, every sweep. VLM on the cameras that matter — the ones on the
user's route, plus any the detector flags as unusual for that corner. That is what fits
inside media-ingest's cadence.

## Open

- Detector arch is `fasterrcnn_resnet50_fpn_v2` because torch+torchvision+CUDA already
  ship in the vLLM container. A YOLO/RT-DETR would likely be faster still and better on
  small distant people; worth a swap if pip access holds.
- Small distant pedestrians (~10 px) are missed by everything at 720x480. That is the
  SDOT ceiling — HLS streams are the same resolution, so no fix exists on our side.
- Night frames with people: only 1 in the sample set. Under-tested.
- No ground truth. Every comparison here is model-vs-model plus eyeballed overlays.
