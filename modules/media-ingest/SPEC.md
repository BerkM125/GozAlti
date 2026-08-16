# modules/media-ingest — Media Data Scraping/Analysis (model's input)

**Owner:** Berkan · **Effort budget:** 3 h

Read `../../SPEC.md` first. Lane: this directory only.

> **Agent-facing API + artifact reference lives in [`README.md`](README.md)** —
> complete `:8030` endpoint table, the offline-vs-online split (what's saved
> locally on the Spark vs what needs upstream fetches), artifact registry, and
> DGX Spark run instructions. Other modules' sessions should read that first;
> this file is the owner-facing spec (scope, contracts, definition of done).

## Scope

Get footage and structure it as the VLM's input feed, as a service on **:8030**:

- **Sources**: SDOT snapshots (all ~646 cameras) and HLS live streams (357
  cameras). Harvest `experiments/safe-walk/safewalk/scraper.py` (sweep daemon,
  dead-camera placeholder detection) and surukamera's stream knowledge
  (`experiments/surukamera/app/manifest.py`, `docs/ENDPOINTS.md` — Wowza URL
  template, stream keys). Optionally the presenter's phone camera as a source
  for the demo.
- **Cadence & priority**: full-city sweep at a slow cadence; en-route/selected
  cameras on a fast lane (safe-walk's `SWEEP_INTERVAL` is 900 s on a laptop,
  60 s on the Spark). Synthesis tells you which cameras are hot
  (`POST :8030/priority` with a camera-id list).
- **Rate-limit discipline is this module's core responsibility**: ≥60 s per
  camera between snapshot fetches, ≤4 concurrent upstream requests, descriptive
  User-Agent, exponential backoff on errors. For HLS, pull short TS segments —
  don't hold hundreds of streams open.
- **Storage & feed**: frames under `data/frames/<camera_id>/<ts>.jpg`
  (gitignored), sized/cropped to what the VLM wants; emit `FrameRecord` (§6.1)
  per capture and push hot-lane frames straight to the VLM (`POST :8040/read`),
  forwarding the returned `Observation` to synthesis.
- Serve `GET :8030/frame/<camera_id>/latest.jpg` so the frontend can show raw
  previews without touching SDOT directly.

**Out of scope:** interpreting imagery (vlm), safety decisions (synthesis),
non-media sources (osint).

## Contracts

Produces `FrameRecord` (§6.1). Set `stale: true` on placeholder detection so the
VLM can skip it.

## Definition of done (demo)

Hot-lane cameras along the demo route refresh every ≤60 s on the Spark; the VLM
receives well-formed `FrameRecord`s continuously; no upstream 429s/bans during a
2-hour run; disk use bounded (prune old frames).

## Practices

- One asyncio process, config knobs via env (mirror safe-walk's `config.py`).
- Log every fetch with camera id + outcome; a per-camera "last success" endpoint
  makes demo-day debugging trivial.
- Test the pipeline against `experiments/surukamera/cache/snapshots/` before
  hitting the live network.

## Quickstart

```bash
cd modules/media-ingest
pip install -r requirements.txt

# 1. build the camera graph artifact (offline, from shipped surukamera data)
python -m ingest.graph            # -> data/camera_graph.json (650 ACTV nodes)

# 2. run the service on :8030
uvicorn ingest.service:app --port 8030

# smoke it
curl "localhost:8030/api/health"
curl "localhost:8030/api/nearby?lat=47.6107&lon=-122.3378&radius_m=150"
curl "localhost:8030/api/convergence?street=Pike%20Street"
curl -o f.jpg "localhost:8030/api/frame/CMR-0270/latest.jpg"

# 3. enrichment builders — RUN THESE WITH THE SERVICE STOPPED (or restart it
#    after): the service also writes camera_graph.json (activity flags), and
#    concurrent writers clobber each other. Both cache their Overpass pulls.
python -m ingest.refuge      # OSM POIs with opening_hours (~4.2k, one pull)
python -m ingest.statics     # alleys/crossings/sidewalk/lit/camera spacing
python -m ingest.setup_cv    # local CNN: pip deps + YOLOv4-tiny (~24MB) + smoke test

# 4. (optional) orientation precompute — sun layers always run; the
#    satellite<->frame VLM reconciliation needs VLM_BASE_URL or ANTHROPIC_API_KEY
python -m ingest.orientation --limit 25

# 5. detection sweep (needs a VLM endpoint; without one, nodes carry no
#    detections — nothing is fabricated)
#    VLM_BASE_URL=http://<spark>:8040/v1 VLM_MODEL=<model>
curl -X POST localhost:8030/api/sweep/start
```

Internal layout: `ingest/graph.py` (camera graph + spatial/street queries),
`feeds.py` (rate-gated snapshots + HLS segment frames, FrameRecord emission),
`orientation.py` + `solar.py` (bearing stack: manual > sat-VLM > corner-token >
sun-history > sun-instant), `activity.py` (binary pixel-activity flag),
`detect.py` (BFS detection traversal, 10 s between passes, hot-lane priority),
`observations.py` + `vlm_forward.py` (temporal breadcrumbs + :8040/read push),
`vlm_client.py` (OpenAI-compatible NIM endpoint, Anthropic fallback),
`service.py` (REST on :8030), `netboot.py` (DNS-over-TCP bootstrap for hostile
venue networks, ported from surukamera).

## Activity flag (attention prior)

Every node carries `activity`: a **binary** pixel-motion flag (median-guarded
MAD between the camera's two most recent frames, downscaled 160×120 grayscale)
computed opportunistically inside the frame path — **zero extra upstream
requests**. Guards: placeholder → `null "stale"`; global luminance shift
subtracted; >60% pixels changed → `null "camera-moved"` + bearing-confidence
downgrade; hysteresis hi=4.0/lo=2.0 (distribution-checked against surukamera's
snapshot cache — verify live, esp. at night); source mismatch (hls vs snapshot)
→ `"no-pair"`. Flags older than `ACTIVITY_MAX_AGE_S` (300 s) read back as
`null`. `active` means exactly "pixel change above threshold between two
timestamped frames" — never "person detected", "busy", or any safety word.
`last_activity_at` on the node feeds co-presence later. The sweep uses the flag
as its hot-lane scheduler (inactive cameras drop to every
`SLOW_LANE_EVERY_N`-th pass) and **runs without any VLM backend** — frames
still get fetched so the flags stay fresh. Persisted in `camera_graph.json`
(throttled saves + at each pass end).

Endpoints: `GET /api/activity` (full-city map, ~0.1 ms from memory),
`active_only=` on `/api/cameras` and `/api/nearby`; `activity` +
`last_activity_at` ride along in camera responses and `convergence()` output
(additive fields next to the §6.7 shape — flag to harness/frontend owners).

## Temporal breadcrumbs (VLM hot lane)

`vlm_forward.read_camera()` pushes hot-lane frames to `POST $VLM_READ_URL`
(:8040/read) as `{"frame_record": <§6.1 untouched>, "image_b64": ...,
"prior_observations": [last ≤3 Observations §6.2, verbatim]}`, records the
returned Observation (ring buffer + `data/observations/*.jsonl`), and forwards
it to `$SYNTH_OBS_URL` if set. **`prior_observations` is a sibling key, not a
§6.1 field — Adi/Dhruv must confirm :8040/read tolerates it; graduating it
into the contract is a god-spec §6 edit with owner sign-off.** Manual trigger:
`POST /api/read/{cid}`; buffer inspection: `GET /api/observations/{cid}`.

Known limitation vs surukamera's stack: the oneway+optical-flow bearing layer
is not ported yet (heaviest layer; needs stream sampling over time).

## Node enrichment — non-LLM datapoints (IMPLEMENTED)

All attached to graph nodes / served per camera; every field carries its
basis. Unknowns stay unknowns — a failed pull or missing tag is never
reported as zero/closed/absent.

- **Refuge / "duck into"** (`ingest/refuge.py` + `hours.py`): OSM POIs with
  `opening_hours` (one cached Overpass pull, 4.2k places; `office=*`
  filtered — not walk-in-able). open/closed evaluated **live** in Seattle
  time by a pragmatic hours parser (unparseable → "unknown", never closed;
  PH/SH holidays not modeled). Honest scope by construction: "N with known
  hours, M open now, nearest open X m" — no completeness claim, OSM-only
  (no Google Places; ToS forbids storing fields). Endpoints:
  `GET /api/refuge?lat&lon&radius_m`, `/api/refuge/bbox`,
  `/api/refuge/street/{name}`. **Lane note**: this static-OSM layer is
  claimed by media-ingest as a graph attribute (like bearings); anything
  requiring live scraping of business sites stays with Dhruv's osint —
  raise at the table if it collides.
- **Co-presence** (`copresence` on nodes): "last person in view" —
  mechanical `max(read_at) where people_count > 0` from forwarded
  Observations (`source: "vlm-observation"`), plus the fast-lane person
  detections (`source: "fastlane-detect"`); frame reference attached.
  Pixel-level `last_activity_at` complements it.
- **Street context** (`ingest/statics.py`, `street_context` on nodes):
  structural OSM facts — sidewalk tags, `lit`, camera spacing along street
  (labeled as camera spacing, NOT "block length"), nearest mapped alley
  distance, crossings within 100 m. Framing rule: alleys are structural
  facts (fewer exits, no camera coverage), never "sketchy". Build:
  `python -m ingest.statics` (Overpass mirror fallback; degrades to
  partial with `source: "osm-partial"`).
- **Sun position**: `GET /api/sun?lat&lon` + per-camera in context
  (NOAA algorithm, deterministic).
- **CameraContext**: `GET /api/context/{cid}` — one doc with everything
  this module knows about a camera (frame §6.1, bearing, activity,
  copresence, street_context, refuge, sun, detections, prior
  observations). Module-internal; graduating it into god-spec §6 is a
  team conversation, and these enrichments becoming `evidence[]` types in
  SegmentAssessment §6.4 needs synthesis sign-off.

Cut deliberately: ground-floor windows / security presence / keycard
access — no queryable source exists; the open-business signal carries the
same meaning with real data. Collision-history normalization stays with
synthesis (denominator honesty: no made-up exposure model).

Dev test UI (not committed): `experiments/berkan_testing/` — served at
`http://localhost:8030/` automatically when the directory exists.
