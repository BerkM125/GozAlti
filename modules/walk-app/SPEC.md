# modules/walk-app

Mobile walking app: React + Bun + Vite, with pedestrian routing computed in-process.

Owner: Dhruv. Consumes media-ingest (`:8030`). Does not touch `modules/map-frontend`.

## Why this module exists alongside map-frontend

`modules/map-frontend` is Ioli's lane and stays untouched.
This module is the mobile-first app: a glass interface on a light-only vector map, a 2D/3D perspective toggle with extruded buildings, a live-camera panel, and detections plotted around the walker.
It also carries its own pedestrian routing, written when `modules/harness` was still SPEC-only.

**Note on the overlap with `modules/harness`.** Harness landed its own A* router in `8a3ad41`, so the repo now has two.
They are not interchangeable: harness builds its graph by fetching Overpass at build time into `modules/harness/data/walk_graph.json`, which is not committed, so it cannot route on a fresh checkout without a network step; this module builds in ~90 ms from `experiments/surukamera/data/osm_ways.json`, which is committed.
Harness also derives `collisions`, `live_penalty`, `confidence` and `stale` from `_jitter()`, a PRNG seeded by OSM way id, which conflicts with the repo rule against numbers that look real but are not.
Which router the demo uses is a call for the team, not something this module should decide unilaterally.
`modules/synthesis/` is still SPEC-only, which is why the alert stream here has nothing to push.

## What is implemented

- **Pedestrian graph** built from `experiments/surukamera/data/osm_ways.json`, the Overpass dump already committed to the repo. 5,435 junctions and 6,579 block-level edges for the downtown bbox, built in ~90 ms with no network call.
- **Dijkstra and A\***, both over the same relaxation loop. A* uses straight-line distance, which is admissible because edge cost is never below edge length. Verified to return the same optimum as Dijkstra while expanding 18 junctions instead of 429.
- **Route API** on `:8020`, `GET`/`POST /api/route`, returning SPEC.md §6.6 `Route` shapes plus per-block §6.4 `SegmentAssessment`s.
- **Mobile UI**: glass panels over a hand-written vector basemap, a 2D/3D toggle with real extruded buildings, tap-to-route, tap-a-block evidence sheet, camera panel, "around you" panel. Light theme only.
- **media-ingest proxy** for cameras, frames, HLS and detections, degrading to an explicit `{ok:false, why}` when it is not running.

## Cost model

`cost = length_m * (1 + riskWeight * risk)` - metres of effective walking. `riskWeight` is 0 for the direct route and 3.0 for the recommended one.

`risk` is four weighted components, each read from a real OSM tag on that block:

| Component | Weight | Source tags |
|---|---|---|
| Sidewalk | 0.34 | `sidewalk`, `sidewalk:both`, `sidewalk:left`, `sidewalk:right` |
| Traffic | 0.28 | `highway` class, `maxspeed` |
| Lighting | 0.20 | `lit` |
| Crossing | 0.18 | `lanes` |

Untagged inputs fall back to a stated neutral default and are marked `inferred`, which the UI renders as "not mapped in OpenStreetMap" rather than passing off as measured. About 72% of downtown blocks have at least one inferred component, almost all of them `lit`.

**Slope is deliberately absent.** Seattle hills matter for walking, but this dump carries no elevation and a fabricated grade would be worse than none.

`segment_id` is `sw:<way_id>:<start_node_id>` - stable across rebuilds. safe-walk's positional integer index renumbers whenever the source data is re-fetched.

Freeways are never routable. Trunk roads are admitted only when OSM positively records a sidewalk.

## Quickstart

```bash
cd modules/walk-app
bun install
bun run smoke        # routes Pike Place -> Pioneer Square, asserts A* == Dijkstra
bun run dev          # API on :8020, Vite on :5173
```

Production, one port, which is what the phone demo needs:

```bash
bun run build
bun run start                                  # serves dist/ + API on :8020
cloudflared tunnel --url http://localhost:8020 # brew install cloudflared first
```

Cameras and detections need media-ingest alongside it:

```bash
cd modules/media-ingest && uvicorn ingest.service:app --port 8030
```

Without it the app still routes; the top bar shows a "cameras offline" chip.

## Endpoints (`:8020`)

| Endpoint | Returns |
|---|---|
| `GET /api/health` | graph size + whether media-ingest is reachable |
| `GET /api/route?from=lat,lon&to=lat,lon&algorithm=astar\|dijkstra` | `RouteResult` |
| `POST /api/route` | same, body `{origin:[lon,lat], dest:[lon,lat], algorithm?}` |
| `GET /api/cameras?lat&lon&radius_m` | §6.7 `CameraConvergence`, proxied |
| `GET /api/detections/:cid` | §6.2 `Observation`, proxied |
| `GET /api/frame/:cid/latest.jpg` | JPEG, proxied |
| `GET /api/hls/*` | HLS passthrough, proxied |
| `GET /api/alerts/stream` | SSE; heartbeats only until synthesis exists |

Every upstream call is proxied through media-ingest. This module never calls SDOT directly, so all rate-limit discipline stays in one place.

## Design rules the UI enforces

- **No aggregate safety score is ever shown.** `risk` is a routing weight (SPEC §6.4) and stays server-side. The sheet shows the four inputs and the tag each came from.
- **Colour budget is four meanings**, using Apple's system palette verbatim: blue `#007AFF` is chrome only and never carries meaning on the map, green `#34C759` is the recommendation, orange `#FF9500` is flagged or unmapped, red `#FF3B30` is reserved for live alerts and refusals. Camera markers are neutral so a real alert stays unmistakable.
- **Every camera image carries an age badge** (`LIVE` / `SNAP 45s` / `SNAP 6m` past 300 s / `NO FRESH FRAME`). Snapshot refresh is 60 s, matching media-ingest's per-camera floor.
- **Detections with no `est` are never placed on the map.** They appear in a "seen, but not placed" group that says the camera's bearing is unresolved.
- **View cones** are drawn only for cameras with a resolved bearing, at opacity scaled by `bearing_conf`.
- **"Ahead" and "behind"** are measured along the route, not from a compass, because the browser cannot get a reliable heading without a permission prompt this app does not need. With no route active the panel says the split is not knowable.

## Basemap

Vector, from OpenFreeMap (OpenMapTiles schema, OSM data, no API key), styled by hand in `src/mapStyle.ts`.
The 3D toggle pitches to 58°, swaps flat building footprints for a `fill-extrusion` layer using each building's real `render_height`, and raises zoom to at least 15 because extrusions only exist from z14.
Raster tiles were tried first and dropped: they carry no building geometry to extrude and no way to control the map's colours.

## Not covered yet

- Live `Alert` banner and map pulse. The SSE endpoint is wired and streams heartbeats, but `modules/synthesis` does not exist, so there is nothing to push. No alert is fabricated to fill the gap.
- Camera and detection rendering is untested against a running media-ingest; it has only been exercised against the offline path.
- Collision and OSINT evidence (§6.3, and the `collision` evidence type) are not wired - synthesis owns those.
- The JS bundle is ~1.87 MB (542 kB gzipped), dominated by MapLibre and hls.js. Fine over a tunnel, worth code-splitting if it matters.
- Downtown OSM tagging is uniform enough that the recommended and direct routes are often identical. That is an honest result, not a bug: the divergence this product is built on comes from the live camera layer, which needs media-ingest and the VLM running.

## Verified 2026-08-15

`bun run smoke` passes all 8 assertions. `bun run build` typechecks and builds clean.
In Chrome at 414×896: 2D and 3D both render, with 683 extruded buildings carrying real heights (79 m, 37 m, 30 m) in the 3D view; tap-to-route returns a real 1.1 km route; tapping the route opens the evidence sheet for `Western Avenue, at Madison Street`, correctly flagging lighting as not mapped in OpenStreetMap.
