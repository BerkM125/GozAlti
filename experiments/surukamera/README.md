# SuruKamera — Seattle Multi-Camera Street Continuity Viewer

Select a street in Seattle; the app positions two public traffic-camera
views so that — where geometry permits — the roadway in one view visually
continues into the roadway of the next. Live HLS video where available
(357 SDOT cameras), snapshots otherwise.

**Non-goal:** this app does not detect, track, or identify people. It is a
road-geometry and situational-awareness tool. All vision runs on road
structure only (line segments, vanishing points, aggregate optical flow).

## Quickstart

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python -m app.manifest        # M0: build data/cameras.json (~2 min)
.venv\Scripts\python -m app.streets         # M1: OSM snap + street index
.venv\Scripts\python -m scripts.run_pairs   # M3: warm pair cache (~10 min, optional)
.venv\Scripts\python -m uvicorn app.server:app --port 8000
# open http://localhost:8000
```

`data/` in this repo is already populated, so `uvicorn` alone is enough to
demo.

Note: outbound networking is routed through an in-process DNS-over-TCP
CONNECT proxy (`app/netboot.py`) because the development network blocked
UDP/53. It works identically on healthy networks.

## How it works

1. **M0 — endpoints** (`app/manifest.py`, `docs/ENDPOINTS.md`): ArcGIS
   feature layer = canonical inventory; snapshot URLs from its `URL` field;
   live HLS from Wowza (`61e0c5d388c2e.streamlock.net`), stream key =
   image filename minus `.jpg`. The stream URL template was recovered from
   the traveler map's own JS (`camera.video.js` → `api/Map/WowsaUrl`);
   playlists serve bare with `Access-Control-Allow-Origin: *`.
2. **M1 — streets** (`app/streets.py`): Overpass arterial network, snap
   each camera to nearest named edge ≤40 m, order cameras along each
   street, candidate pairs at 60–500 m. 472 snapped cameras, 105 streets
   with ≥2 cameras, 124 candidate pairs.
3. **M2 — geometry** (`app/geometry.py`): recomputed per fetched frame,
   cached by `(camera_id, image_hash)` — never per camera, because these
   are PTZ cameras that operators re-aim. Line-segment VP (LSD + RANSAC)
   cross-checked against a flow-derived VP from one live TS segment
   (vehicles move along the road; crosswalk stripes that poison line
   detection don't move). Flow wins when they disagree.
3b. **Bearing stack** (`app/bearing.py`): the road AXIS comes from the OSM
   snap; a layered stack decides which of the two 180° directions the
   camera faces, in priority order — every layer's verdict is shown in the
   diagnostics rail:
   - **L0 manual** (0.95): operator confirms via 8-direction buttons +
     satellite crop in the UI; stored in `data/manual_bearings.json`.
   - **L1 oneway+flow** (≤0.90): optical flow (approach/recede) + mapped
     travel direction on one-way streets.
   - **L2 satellite VLM** (0.80): Claude vision compares the frame against
     an annotated Esri World Imagery crop with the two hypothesis arrows
     (`app/vlm.py`; behind `VISION_API_KEY`/`ANTHROPIC_API_KEY`, cached
     per camera, silently skipped without a key).
   - **L3 landmark VLM** (0.75): Space Needle / Smith Tower / Columbia
     Center / stadiums / Mt Rainier (SSE) / Olympics (W) / Cascades (E)
     spotted left/center/right in frame; heading ≈ bearing-to-landmark −
     frame offset. Enum-only schema, road structure and skyline only.
   - **L3b corner token** (0.5): SDOT key suffixes `NWC/NEC/SWC/SEC` mark
     the intersection corner the pole stands on; the camera points across
     the intersection (found via the safe-walk repo crawl — its data has
     no bearings; these tokens were latent and unparsed).
   - **L4 sun history** (0.55): per-camera overexposure peak across the
     local snapshot archive vs exact solar azimuth (NOAA solar position,
     `app/solar.py`); strengthens automatically as the cache grows.
   - **L5 sun instant** (0.35–0.5): glare / sky-brightness asymmetry on
     the current frame vs the sun's position right now.
   - **Fallback**: unresolved → pair-level 180° hypothesis testing with
     capped confidence and the basis stated.
   A `NS`/`EW` key-token cross-check penalizes bearings whose OSM snap
   axis disagrees with the camera's named approach axis.
4. **M3 — classification** (`app/pairs.py`): heading delta <45° =
   CO_DIRECTIONAL (stitch attempt), 135–180° = OPPOSED (never stitched,
   plain split), else OBLIQUE; low confidence / PTZ pan / drift =
   UNKNOWN → split. Continuity score = 0.35·heading + 0.25·VP-x +
   0.15·horizon + 0.15·slope + 0.10·distance; ≥0.6 stacks.
5. **M4/M5 — layout + UI** (`app/server.py`, `web/`): stacked continuity
   view (downstream on top, panes rotated about their bottom-centre so the
   vanishing point sits on the shared vertical axis, feathered seam
   labelled with the real gap) or honest split screen. Dark-console UI:
   street rail, MapLibre map with per-camera view cones (opacity =
   confidence — they swing when a PTZ camera is re-aimed), diagnostics
   rail with the full decision reason. Panes are adjustable: drag the seam
   (stacked) or divider (split) to resize, drag the map edge to change the
   map/viewport split, drag inside a feed to pan, scroll to zoom,
   double-click to reset — adjustments survive the 60 s refresh.

## Known risks (stated honestly)

- **Coverage**: cameras sit on arterials. Residential streets have no
  coverage and never will.
- **PTZ drift**: headings are per-frame estimates, not calibration. A pan
  is detected in seconds from uniform optical flow and suspends
  continuity; slower drift is caught by comparing consecutive samples
  (>25° bearing / >0.25 VP-x shift → STALE_GEOMETRY → split until two
  samples agree).
- **Non-synchronous panes**: applies to snapshot-backed pairs only — those
  views are minutes apart and the composition is a spatial aid. Stream-
  backed pairs are near-simultaneous. The UI labels the two cases (LIVE
  vs SNAP badge + fetch time per pane) and snapshot pairs never inherit
  the stream framing.
- **Night and rain**: VP detection degrades badly after dark. Expect
  confidence collapse and split-screen fallback — that is designed
  behaviour, not a bug.
- **No archive**: snapshots are ephemeral upstream; `cache/snapshots/` is
  the only history.
- **Direction ambiguity**: on two-way streets without usable flow, the
  180° view-direction ambiguity is resolved by hypothesis testing at pair
  level. The diagnostics rail always states which basis was used.

## Data / attribution

Camera imagery and inventory: City of Seattle, Seattle Department of
Transportation. Basemap: © OpenStreetMap contributors, © CARTO. Street
graph: OpenStreetMap via Overpass. Fetch discipline: ≥60 s between
snapshot fetches per camera, ≤4 concurrent upstream requests, descriptive
User-Agent.
