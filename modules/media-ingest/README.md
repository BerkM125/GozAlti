# media-ingest — camera context service on `:8030`

**Owner: Berkan Mertan** · runs **locally on the DGX Spark**, same box as the
vlm module (`:8040`). This module is the single source for *everything about
Seattle's public cameras and their surroundings*: which cameras exist, where
they point, what streets they see, whether anything is moving in view, what
businesses are open nearby, structural street facts, satellite imagery,
building footprints, live frames and clips.

> ## ⚡ If you are an agent (Adi / Dhruv / Ioli sessions — read this first)
>
> **Do NOT scrape the web for camera, street, business-hours, building, or
> satellite data. It is already here, on this box, most of it offline.**
> Hitting SDOT/OSM/Esri yourself wastes wall-clock time, risks rate-limit
> bans (a project-ending failure, SPEC §7.5), and duplicates work.
>
> - Need everything about one camera in one call → `GET :8030/api/context/{cid}`
> - Need cameras for a point/street → `/api/nearby`, `/api/convergence`, `/api/street/{name}`
> - Need a frame or live video → `/api/frame/{cid}/latest.jpg`, `/api/hls/{key}/playlist.m3u8`
>   (this module owns the rate gates — never fetch from SDOT/Wowza directly)
> - Need open businesses near a point, evaluated *right now* → `/api/refuge`
> - The graph artifact itself is plain JSON at
>   `modules/media-ingest/data/camera_graph.json` if you need bulk offline reads.
>
> The tables below say exactly what is **OFFLINE** (instant, no network) vs
> **ONLINE** (upstream fetch, rate-gated). Anything marked offline answers in
> milliseconds from memory/disk on the Spark.

---

## 1. What's OFFLINE vs what's ONLINE

### 1.1 OFFLINE — saved artifacts + pure computation (no internet at query time)

| Capability | Where it lives | How it answers |
|---|---|---|
| Camera graph: 646 ACTV cameras, full SDOT metadata, adjacency (street + proximity edges), grid spatial index | `data/camera_graph.json` (~1 MB) | In-memory `CameraGraph`; `nearby()` / `street()` / `convergence()` are exact and instant |
| Street ↔ camera mapping (street → ordered cameras, point → best street) | same artifact (`street_index`) | `/api/streets`, `/api/street/{name}`, `street_near` in `/api/nearby` |
| Camera bearings / FOV directions (manual > sat-VLM > corner-token > sun layers) | on each node + `data/manual_bearings.json`, `data/satellite_vlm.json` | precomputed; served on every camera response |
| **Activity flags** — binary pixel-motion per camera (attention prior) | on each node, persisted to the graph artifact | computed opportunistically from frames already fetched; `/api/activity` answers city-wide from memory |
| **Open businesses ("refuge")** — ~4.2k named OSM POIs with `opening_hours` | `data/refuge_pois.json` (~0.9 MB) | open/closed evaluated **live in Seattle time from the stored hours spec** — the clock is local, no network involved |
| **Street context** — sidewalk tags, `lit`, camera spacing, nearest alley distance, crossings within 100 m | `street_context` on each node (built by `ingest/statics.py`, cache `data/osm_alleys.json`) | plain node fields |
| Co-presence — "last person seen in view" (from round-tripped Observations + fast-lane detections) | `copresence` on each node | plain node fields |
| Sun position (azimuth/elevation, daylight) | none needed — deterministic NOAA algorithm | `/api/sun` computes it |
| Last detections per camera (what the sweep last saw) | memory + `data/live_state.json` | `/api/detections` |
| Observation breadcrumbs (last ≤3 VLM Observations per camera, verbatim) | memory + `data/observations/*.jsonl` | `/api/observations/{cid}` |
| Cached frames / clips already fetched | `data/frames/`, `data/segments/` | `/api/frame/{cid}/latest.jpg` serves cache when upstream is gated/down |
| Cached map tiles, satellite tiles, satellite crops, building footprints | `data/tiles/`, `data/sat_tiles/`, `data/satellite/` | served from cache after first fetch |

### 1.2 ONLINE — upstream fetches (rate-gated; only this module does them)

| Capability | Upstream | Discipline (hard-coded) |
|---|---|---|
| Fresh snapshot per camera | SDOT | ≥ 60 s per camera (`SNAPSHOT_MIN_INTERVAL_S`), ≤ 4 concurrent, descriptive User-Agent |
| Live HLS video / newest TS segment frames | Wowza (`streamlock.net`) | ≥ 10 s per camera (`HLS_MIN_INTERVAL_S`); playlist reads can transiently time out → falls back to snapshot/disk cache by design |
| First-time map tile / satellite tile / satellite crop | Carto / Esri | cached forever after first fetch |
| First-time building footprints for a bbox | Overpass | cached per rounded bbox; bbox size capped |
| VLM reads (`/api/analyze`, `/api/read`, sweep) | `:8040` on this same box | **local network, not internet**; `VLM_CONCURRENCY` cap |

### 1.3 One-time builders (need internet ONCE, then the artifact is offline forever)

Run with the service **stopped** (both writer paths touch `camera_graph.json`):

```bash
python -m ingest.graph      # NO internet — builds from shipped experiments/surukamera/data/
python -m ingest.refuge     # ONE Overpass pull  -> data/refuge_pois.json
python -m ingest.statics    # ONE Overpass pull  -> data/osm_alleys.json + street_context on nodes
python -m ingest.orientation --limit 25   # optional; sun layers offline, sat-VLM layer needs a VLM
```

`modules/*/data/` is **gitignored** — on a fresh clone (including on the Spark)
run the builders once. `ingest.graph` needs zero network; the other two cache
their single Overpass pull and never re-fetch while the cache file exists.

---

## 2. Endpoint reference (`http://<spark>:8030`)

All responses JSON unless noted. Every enrichment field carries its
`basis`/`source` — honesty rules in §4.

### 2.1 Camera discovery & graph queries — OFFLINE

| Endpoint | What it returns |
|---|---|
| `GET /api/health` | module status, graph counts, sweep status |
| `GET /api/cameras?active_only=` | all cameras, light form (id, lat/lon, street, neighborhood, bearing, activity, copresence, street_context, hls/snapshot links) |
| `GET /api/camera/{cid}` | one full graph node + live detection state |
| `GET /api/nearby?lat=&lon=&radius_m=100&active_only=` | cameras within radius, nearest first, + `street_near` (best street for the point) |
| `GET /api/convergence?lat=&lon=&radius_m=300` or `?street=Pike%20Street` | **`CameraConvergence` §6.7** — street/area → cameras with bearings + live URLs (activity fields ride along additively) |
| `GET /api/streets` | street name → camera count index |
| `GET /api/street/{name}` | ordered cameras along that street |
| `GET /api/activity` | full-city `{camera_id: activity}` map, < 100 ms from memory. **Pixel-change signal only — never a people/safety claim** |

### 2.2 Frames & live video — ONLINE (rate-gated), cache-backed

| Endpoint | What it returns |
|---|---|
| `GET /api/frame/{cid}/latest.jpg` | newest JPEG frame (fetches upstream only if the per-camera gate allows; otherwise serves disk cache) |
| `GET /api/frame/{cid}/record` | its **`FrameRecord` §6.1** (`stale: true` on dead-camera placeholder) |
| `GET /api/hls/{key}/playlist.m3u8` (and `/api/hls/{key}/{path}`) | HLS proxy — play live video without touching Wowza directly; `key` comes from any camera response |

### 2.3 Enrichment / context — OFFLINE

| Endpoint | What it returns |
|---|---|
| **`GET /api/context/{cid}`** | **CameraContext: everything this module knows about one camera in one document** — frame §6.1, bearing, activity, copresence, street_context, refuge (open businesses now), sun, detections, prior observations. **This is the endpoint to reach for first when assembling VLM context.** |
| `GET /api/refuge?lat=&lon=&radius_m=150` | open businesses to duck into near a point: `n_known_hours`, `n_open_now`, `nearest_open`, POI list with `open_now`/`open_until` evaluated live in Seattle time |
| `GET /api/refuge/bbox?s=&w=&n=&e=` | evaluated POIs in a bbox (map layer) |
| `GET /api/refuge/street/{name}` | refuge summary aggregated along a street's cameras |
| `GET /api/sun?lat=&lon=` | solar azimuth/elevation + `is_daylight` right now (deterministic NOAA) |

### 2.4 Detections & the VLM hot lane

| Endpoint | What it does |
|---|---|
| `GET /api/detections` / `/api/detections/{cid}` | last sweep analysis per node (objects + rough world positions via `fov-projection` when bearing is resolved) — OFFLINE read |
| `POST /api/analyze/{cid}` | analyze one camera right now (fresh frame → VLM on `:8040`) |
| `POST /api/read/{cid}` | hot-lane push to `POST $VLM_READ_URL` (`:8040/read`) as `{"frame_record": §6.1 untouched, "image_b64": ..., "prior_observations": [≤3 §6.2 verbatim]}`; records + forwards the returned Observation |
| `GET /api/observations/{cid}` | the breadcrumb ring buffer (verbatim Observations) — OFFLINE read |
| `POST /api/priority {"camera_ids": [...]}` | mark hot-lane cameras (en-route) — processed first every pass |
| `POST /api/sweep/start` / `/api/sweep/stop`, `GET /api/sweep/status` | BFS traversal loop over all cameras, 10 s rest between passes; activity flag drives the hot/slow lanes; **runs with no VLM backend too** (fetch-only passes keep activity flags fresh) |

### 2.5 Orientation & imagery

| Endpoint | What it does |
|---|---|
| `GET /api/satellite/{cid}?zoom=18&annotate=` | satellite crop centered on the camera (bearing arrows optional) — cached after first fetch |
| `POST /api/bearing/{cid} {"bearing_deg": 315}` | human-confirmed FOV direction (calibration UI) — persisted |
| `DELETE /api/bearing/{cid}` | clear manual, back to auto layers |
| `POST /api/orient/{cid}` | re-run the bearing stack on one camera |
| `GET /api/tile/{z}/{x}/{y}` / `GET /api/sat-tile/{z}/{x}/{y}` | dark basemap / Esri satellite tile proxies, cached |
| `GET /api/buildings?s=&w=&n=&e=` | OSM building footprints as GeoJSON with heights (3D layer), cached per bbox |

---

## 3. The artifacts, precisely

All under `modules/media-ingest/data/` (gitignored, rebuildable):

| File | Size | Contents | Built by | Internet to build? |
|---|---|---|---|---|
| `camera_graph.json` | ~1 MB | **The core artifact.** 646 ACTV camera nodes: full SDOT metadata, lat/lon, street snap + `street_index`, adjacency edges, `bearing`, `activity` + `last_activity_at`, `copresence`, `street_context` | `python -m ingest.graph`, then enriched in place by the service + builders | **No** (shipped surukamera data) |
| `refuge_pois.json` | ~0.9 MB | ~4.2k named OSM POIs with `opening_hours` (offices filtered — not walk-in-able) | `python -m ingest.refuge` | Once |
| `osm_alleys.json` | ~9.7 MB | alley centerlines + pedestrian crossings cache for statics | `python -m ingest.statics` | Once |
| `manual_bearings.json` | tiny | human-confirmed bearings | calibration UI | No |
| `satellite_vlm.json` | tiny | cached sat↔frame VLM verdicts | orientation precompute | via VLM |
| `frame_records.jsonl` / `live_state.json` / `observations/*.jsonl` | grows | FrameRecord log, last detections, Observation breadcrumbs | service runtime | — |
| `frames/` `segments/` `satellite/` `tiles/` `sat_tiles/` | grows | JPEG frames, raw TS segments (clip base), imagery caches | service runtime | as fetched |

Reading `camera_graph.json` directly is supported for bulk offline work — it's
plain JSON. But **write** to it only through this module (the service and the
builders coordinate saves; concurrent writers clobber each other).

## 4. Honesty rules (binding, inherited from the god spec)

- `activity.active` means exactly "pixel change above threshold between two
  timestamped frames" — never "person detected", "busy", or any safety word.
  Stale/moved cameras report `null`, not `false`.
- Refuge counts are scoped: "N places *with known OSM hours*, M open now" —
  never a completeness claim. Unparseable hours → `open_now: null` (unknown),
  never closed. Holidays (PH/SH) not modeled.
- Street context: alleys are structural facts (fewer exits, no camera
  coverage), never "sketchy". `camera_gap_m` is camera spacing, not "block
  length". A failed OSM pull is `null` (unknown), never zero.
- Detection positions are rough monocular estimates labeled
  `method: "fov-projection"`; unresolved bearing → no position, not a guess.
- No VLM backend → nodes simply carry no detections. Nothing is fabricated.

## 5. Contracts & integration points

- **Produces `FrameRecord` §6.1** (exact shape, `stale` flag honored).
- **`/api/convergence` returns `CameraConvergence` §6.7**; `activity` /
  `last_activity_at` are *additive* sibling fields — flagged to harness/frontend.
- **Hot-lane body to `:8040/read`**: `prior_observations` is a **sibling key**
  next to the untouched §6.1 record — Adi/Dhruv must confirm the endpoint
  tolerates it; graduating it into §6 is a god-spec edit with owner sign-off.
- `CameraContext` (`/api/context/{cid}`) is module-internal; promoting it (or
  refuge/street-context as `evidence[]` types in §6.4) needs a team edit.
- Synthesis marks en-route cameras hot via `POST /api/priority`.
- Set `SYNTH_OBS_URL` to have every round-tripped Observation forwarded to
  synthesis automatically.

## 6. Running on the DGX Spark

```bash
cd modules/media-ingest
python -m venv .venv && . .venv/bin/activate     # (Windows dev box: py -3 -m venv; use the module venv, not system python)
pip install -r requirements.txt

# one-time artifact builds (service stopped; ~2 min total, Overpass pulls cached)
python -m ingest.graph
python -m ingest.refuge
python -m ingest.statics

# env (only the VLM knobs are required for model calls; everything else has defaults)
export VLM_BASE_URL=http://localhost:8040/v1     # OpenAI-compatible NIM on this box
export VLM_MODEL=nvidia/cosmos-reason1-7b
export VLM_READ_URL=http://localhost:8040/read   # hot-lane push target
export SYNTH_OBS_URL=http://localhost:8020/api/observations   # optional

uvicorn ingest.service:app --host 0.0.0.0 --port 8030
curl -X POST localhost:8030/api/sweep/start      # keeps frames + activity flags fresh

# smoke
curl "localhost:8030/api/health"
curl "localhost:8030/api/context/CMR-0270"
curl "localhost:8030/api/refuge?lat=47.6107&lon=-122.3378&radius_m=150"
```

All config knobs are env-overridable — see [`ingest/config.py`](ingest/config.py)
for the complete list (rate gates, activity thresholds, sweep cadence, VLM
endpoints, retention).

## 7. Internal layout

`ingest/graph.py` camera graph + spatial/street queries · `feeds.py` rate-gated
snapshots + HLS segment frames, FrameRecord emission · `activity.py` binary
pixel-activity flag · `detect.py` BFS detection sweep + hot lane ·
`orientation.py` + `solar.py` bearing stack + sun · `refuge.py` + `hours.py`
open-business layer + OSM hours evaluator · `statics.py` street-context
builder · `observations.py` + `vlm_forward.py` breadcrumbs + `:8040/read`
push · `vlm_client.py` OpenAI-compatible VLM (Anthropic fallback) ·
`service.py` REST on `:8030` · `netboot.py` DNS-over-TCP bootstrap for hostile
venue networks · `config.py` every knob.

Deeper spec, definition of done, and per-feature docs: [`SPEC.md`](SPEC.md).
