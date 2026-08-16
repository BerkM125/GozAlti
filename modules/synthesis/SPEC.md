# modules/synthesis — Data Synthesis (model's output, downstream)

**Owner:** All (whoever finishes their module first picks this up; pair on it)
· **Effort budget:** 3 h

Read `../../SPEC.md` first. This module is the integration point — it imports
harness and talks to every service, but still doesn't edit other modules' code.

## Scope

The single backend the frontend talks to, on **:8020**:

- **Combine evidence** into `SegmentAssessment` (§6.4):
  - static layer: safe-walk's per-segment collision/sidewalk risk (via harness);
  - live layer: recent `Observation`s mapped camera→segment, freshness-capped
    (harvest the capped-nudge idea from `experiments/safe-walk/safewalk/live.py`
    — a live read *nudges* risk, it never dominates);
  - osint layer: `AreaSignal`s mapped area→segments with time decay.
  - Every contribution becomes an `evidence[]` entry. `risk` is a routing
    weight; the UI only ever shows evidence.
- **Serve the frontend**: `GET /api/route?from&to` (calls harness with current
  risks), `GET /api/cameras?street|lat,lon`, `GET /api/segment/<id>`
  (assessment + evidence), `GET /api/observations/<camera_id>/latest`, plus
  static file serving of the built frontend.
- **Live alerts**: watch incoming `Observation`s; when a hot-lane camera's state
  changes materially (e.g. gains `blocked_sidewalk`, crowd spike vs its own
  baseline), emit `Alert` (§6.5) on the SSE stream `/api/alerts/stream` and tell
  media-ingest which cameras are hot (`POST :8030/priority`) based on active
  routes. This closes the LIVE loop the demo depends on.

**Out of scope:** fetching anything upstream (media-ingest/osint), running
models (vlm), map rendering (frontend), graph algorithms (harness).

## Contracts

Consumes `Observation`, `AreaSignal`, harness's `Route`/`CameraConvergence`.
Produces `SegmentAssessment`, `Alert`, and the HTTP API above.

## Definition of done (demo)

With all services up: route request returns evidence-backed routes; segment
click returns real mixed evidence (static + vlm + osint); a staged change on an
en-route camera produces an alert on a phone within one hot-lane cycle.

## Practices

- FastAPI + pydantic models generated 1:1 from SPEC §6 — this module is the
  contract enforcer; reject invalid input loudly with the producing module named
  in the error.
- In-memory state + JSONL append log; no database.
- Build against fixture `Observation`/`AreaSignal` files from day one so
  integration doesn't wait on upstream modules; keep the fixtures as tests.
- Alert thresholds conservative: one good alert in the demo beats banner spam.
