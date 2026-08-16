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
- **Destination search**, offline.
  `GET /api/geocode?q=` answers from the walk graph itself: one entry per named street, one per junction where two named streets cross (3,082 places), with street abbreviations ("st", "3rd", "ne") and cross-street queries ("3rd & pine", "pike and 1st") normalised in `server/search.ts`.
  No external geocoder, no API key, no network call, and every result is by construction routable.
- **Mobile UI**: a Google-Maps-style trip planner (search a destination, editable start and end, swap, "Choose on the map"), glass panels over a hand-written vector basemap, a 2D/3D toggle with real extruded buildings, tap-to-route, tap-a-block evidence sheet, a bottom "Live feeds" tab opening a vertically scrolled camera feed, and an "around you" panel.
  Light theme only, Inter (self-hosted, `public/fonts/`) as the one type family on every device.
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

Untagged inputs fall back to a stated neutral default and are marked `inferred`, which the UI renders as a "not mapped" chip rather than passing off as measured. About 72% of downtown blocks have at least one inferred component, almost all of them `lit`.

The observed `risk` spread across the 6,579 blocks is 0.08-0.67, with 74% of blocks between 0.15 and 0.35.
The UI's weight ramp therefore interpolates over 0.08-0.56 (`src/palette.ts` `WEIGHT_STOPS`, mirrored as `--ramp` in `theme.css`) rather than the nominal [0,1], which would paint the whole city one colour.
Each `SegmentAssessment.evidence` item now also carries its component `score` (2 dp), which the sheet's per-input dots colour from the same ramp.

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

Cameras and detections need media-ingest alongside it. On a fresh clone its `data/` is gitignored, so build the camera graph once - no network needed, it reads the committed `experiments/surukamera/data/`:

```bash
cd modules/media-ingest
python3 -m venv .venv && ./.venv/bin/pip install -r requirements.txt
./.venv/bin/python -m ingest.graph          # ~650 cameras, offline, prints counts
./.venv/bin/uvicorn ingest.service:app --port 8030
```

Without it the app still routes; the top bar shows a "cameras offline" chip.

## Endpoints (`:8020`)

| Endpoint | Returns |
|---|---|
| `GET /api/health` | graph size + whether media-ingest is reachable |
| `GET /api/route?from=lat,lon&to=lat,lon&algorithm=astar\|dijkstra` | `RouteResult` |
| `GET /api/geocode?q=pike+st` | `{query, results:[{label, kind, lat, lon}]}` - streets and intersections from the walk graph, offline |
| `POST /api/route` | same, body `{origin:[lon,lat], dest:[lon,lat], algorithm?}` |
| `GET /api/blocks` | GeoJSON FeatureCollection of every walkable block, `{segment_id, risk}` per feature; built once at boot, pre-gzipped (~300 kB). Feeds the map's routing-weight layer |
| `GET /api/segment/:segment_id` | one block's §6.4 `SegmentAssessment`, for the tap-anywhere evidence sheet. 404 for an unknown id |
| `GET /api/cameras?lat&lon&radius_m` (or `?street=`) | §6.7 `CameraConvergence`, built here from media-ingest |
| `POST /api/cameras/route` | every camera watching a route, in passing order. Body `{polyline:[[lat,lon],…], radius_m?}` |
| `GET /api/detections/:cid` | media-ingest's live-state record, proxied |
| `GET /api/frame/:cid/latest.jpg` | JPEG, proxied |
| `GET /api/frame/:cid/record` | §6.1 `FrameRecord`, proxied - what the age badge reads |
| `GET /api/hls/*` | HLS passthrough, proxied |
| `GET /api/alerts/stream` | SSE; heartbeats only until synthesis exists |

Every upstream call is proxied through media-ingest. This module never calls SDOT directly, so all rate-limit discipline stays in one place.

### Cameras on a route

A route is a line, not a point.
Asking for the cameras "near" one point on it - you, or the route's midpoint - only ever finds the handful by that point, which is why the app used to show the same three cameras whether the walk was 500 m or 3 km.

`POST /api/cameras/route` measures every camera against the actual polyline and returns the ones within `ROUTE_CORRIDOR_M` (default 180 m, `ROUTE_CORRIDOR_M` env), ordered by how far along the walk they sit:

- `distance_m` becomes the perpendicular distance to the route - "how far off my way is this".
- `along_m` is how far into the walk the camera sits, and is what the ordering uses, so the list reads in the order you pass them.

There is **no cap** when a route is active: a camera at the far end matters as much as one at the start.
Without a route the panel falls back to a radius around you, capped at `NEARBY_CAMERA_LIMIT`.

The corridor is computed here rather than by sampling the route with repeated radius queries, which would both miss cameras between samples and double-count the ones near them.
media-ingest answers `GET /api/cameras` with all 646 cameras from memory in ~30 ms and no upstream call, so this server caches that list for 5 minutes and measures against the whole set at once.
If media-ingest goes down with a warm cache the layer keeps working from it - camera positions are static graph data - while the age badge and frame still fail honestly, so nothing stale is passed off as current.

Measured: 517 m route -> 12 cameras, 1.1 km -> 19, 2.8 km -> 36. `Route.cameras_en_route` (§6.6) is filled from the same corridor.

### The one documented deviation from §6.7

`/api/cameras` returns the §6.7 `CameraConvergence` **shape**, but `live_hls` and `snapshot_url` are this server's own same-origin paths (`/api/hls/<key>/playlist.m3u8`, `/api/frame/<cid>/latest.jpg`) rather than the absolute `streamlock.net` and `seattle.gov` URLs §6.7's example shows.

This is deliberate, and it is why the endpoint sources media-ingest's `/api/nearby` (and `/api/street/{name}`) instead of its `/api/convergence`:

- `/api/convergence` emits the raw upstream Wowza URL and omits `key`. Handing that to a browser means the phone streams straight from SDOT, which breaks invariant #7 and root `SPEC.md` §6.9 ("No other module talks to SDOT/Wowza/Overpass/Esri directly"). A browser running this bundle *is* this module.
- `/api/nearby` carries `key`, ready-made proxy-relative URLs, a server-measured `dist_m`, and the `desc`/`street` the UI captions with. It is the same shape `experiments/berkan_testing` consumes.

The field names, types and nullability are unchanged - `live_hls` is still `null` for a snapshot-only camera. media-ingest keeps emitting §6.7 exactly as specified; only this module's own boundary substitutes the value. No shared contract was edited.

**The proxy is not a rate limit.** media-ingest's `/api/hls/{key}/{path}` is a straight passthrough with no gate. Routing through it satisfies invariant #7 and keeps one User-Agent and one place to add a gate later, but the actual load control is the rule below.

## Design rules the UI enforces

- **`risk` is shown as what it is - a routing weight - and never as a safety verdict.** The default map layer colours every block by its weight, the legend and the block sheet's scale bar say "routing weight" (never "safety" or "danger score"), no aggregate number is printed anywhere, and tapping any block shows where it sits on the scale plus the four inputs behind it.
  Documented deviation: root SPEC §6.4 still says everything shown to a user comes from `evidence[]`, never bare `risk`; the module owner is raising that change with the team, and the shared file is not edited from this module.
- **Colour carries fixed meanings**: blue `#007AFF` is the recommended route and the chrome. The muted green-to-brick weight ramp (`palette.ts WEIGHT_STOPS` / `--ramp`) carries routing weight and nothing else. Green `#34C759` marks only a live/healthy camera feed. Orange `#FF9500` is flagged or unmapped in UI fills. Solid red `#FF3B30` fills stay reserved for live alerts and refusals. Camera markers are neutral so a real alert stays unmistakable.
- **Only one camera streams live at a time.** Tiles in the list are snapshots; live HLS plays only in the full-screen viewer, for the one camera the user opened. Each playing tile is a continuous viewer on SDOT's stream host, and nothing upstream throttles that, so the count is held at one. A stream-capable camera showing a still says so and offers `▶ LIVE`; it never shows `LIVE` over a still frame.
- **Every camera image carries an age badge**, read from the §6.1 `FrameRecord` (`GET /api/frame/:cid/record`), not from the VLM. A camera with no VLM read still has a timestamped frame, so the badge works with no model in the loop: `LIVE` while playing, else `STREAM 6s` / `SNAP 45s` / `CACHED 12m` naming the frame's `source`, `NO FRESH FRAME` when `stale`, `AGE UNKNOWN` when no record has arrived. Snapshot refresh is 60 s, matching media-ingest's per-camera floor.
- **Detections with no `est` are never placed on the map.** They appear in a "seen, but not placed" group that says the camera's bearing is unresolved.
- **View cones** are drawn only for cameras with a resolved bearing, at opacity scaled by `bearing_conf`.
- **"Ahead" and "behind"** are measured along the route, not from a compass, because the browser cannot get a reliable heading without a permission prompt this app does not need. With no route active the panel says the split is not knowable.
- **The camera list never claims to be "near you" when it is not.** Without a position it is titled "Cameras downtown" and states why, rather than passing downtown cameras off as local ones.
- **Search never invents places.** Suggestions come only from the routable walk graph, so a match can always be walked to; "Your location" is offered only when a position actually exists, and an empty result says the downtown walking map has no match rather than pretending to search the whole city.

## Location

The app asks for a position once on load and shows the nearest cameras to it, closest first, using the distance media-ingest measured.
The MapLibre `GeolocateControl` stays for re-centring.
The first fix eases the map to you and drops a blue dot; later updates do not move the camera, so they cannot fight your panning or yank a framed route off screen.

With a position known, **one tap routes**: the tap sets the destination and the walk starts from you.
Searching a destination does the same: picking a place fills the destination and the start defaults to "Your location" when a position exists, or stays an explicit "Set your start" when it does not - a start is never guessed.
With a full route on screen, a further tap re-anchors the start and **keeps the destination**: the destination is the part of the trip the user typed, so a stray tap must not throw it away.
Clearing the whole trip is the planner's explicit close button.
The planner's "Choose on the map" arms the next tap to fill exactly that field instead.
With no position the old tap-start-then-tap-destination flow is unchanged.

When the position cannot be had, the app falls back to downtown for the camera query and says so.
It distinguishes the causes, because one of them has a fix:

**Geolocation needs a secure context.** It works on `localhost`, and it silently fails over a plain-HTTP LAN address - which is exactly how a phone reaches `bun run dev`. The app detects `!window.isSecureContext` and names it instead of reporting a generic denial. For a phone, use the tunnel:

```bash
bun run build && bun run start
bun run tunnel      # cloudflared, terminates HTTPS
```

## Basemap

Vector, from OpenFreeMap (OpenMapTiles schema, OSM data, no API key), styled by hand in `src/mapStyle.ts`.
The 3D toggle pitches to 58°, swaps flat building footprints for a `fill-extrusion` layer using each building's real `render_height`, and raises zoom to at least 15 because extrusions only exist from z14.
Raster tiles were tried first and dropped: they carry no building geometry to extrude and no way to control the map's colours.

## Not covered yet

- Live `Alert` banner and map pulse. The SSE endpoint is wired and streams heartbeats, but `modules/synthesis` does not exist, so there is nothing to push. No alert is fabricated to fill the gap.
- Detections and captions need a VLM behind media-ingest. Wiring `VLM_BASE_URL` at the local Ollama does not currently work: `qwen3-vl:8b` is a reasoning model and returns its answer in `reasoning` with an empty `content`, which media-ingest's parser reads as no result. Until that is resolved with the media-ingest owner, every camera honestly reports "no camera read yet"; nothing is fabricated to fill the gap.
- Bearings are `null` for every camera on a fresh `ingest.graph` build, so there are no view cones and every detection would land in "seen, but not placed". That is invariants #3 and #4 working, not a bug. Resolving them needs media-ingest's `POST /api/orient/{cid}`.
- Collision and OSINT evidence (§6.3, and the `collision` evidence type) are not wired - synthesis owns those.
- The JS bundle is ~1.87 MB (542 kB gzipped), dominated by MapLibre and hls.js. Fine over a tunnel, worth code-splitting if it matters.
- Downtown OSM tagging is uniform enough that the recommended and direct routes are often identical. That is an honest result, not a bug: the divergence this product is built on comes from the live camera layer, which needs media-ingest and the VLM running.

## Verified 2026-08-16 - destination search and the UI refresh

Run in Chromium at 414x896 against the production build with media-ingest live:

- `bun run smoke` passes 13 assertions, the 5 new ones covering search: "pike st" resolves to Pike Street, "3rd and pine" resolves to the Pine Street & 3rd Avenue intersection, that result routes, sub-2-char queries return nothing, all coordinates finite.
- Typing "pike pl" in the destination bar shows ranked suggestions with kind labels; picking one fills the planner and (with no position available) the app asks for a start instead of guessing one.
- Setting the start via search produces a real 1.5 km / 19 min recommended route vs 1.3 km / 17 min direct, camera markers along it, and the feeds tab count moving 12 -> 31.
- Swap reverses the stops and re-routes; a map tap with a route active re-anchors the start as "Dropped pin" and keeps the searched destination; "Choose on the map" arms exactly one tap for exactly the chosen field, with a cancellable hint pill.
- The feeds tab expands into the vertical feed: full-width tiles, real frames, age badges, "31 watching your way · in passing order"; opening a camera plays exactly one live HLS stream.
- "Pike Place" returns no result because the OSM dump has no walkable way of that name in the bbox - an honest gap, stated in the empty state, not papered over.
- Console is clean apart from Chromium's software-WebGL warning.

The horizontal camera carousel is gone: feeds scroll vertically in the sheet, which is the thumb direction on a phone.
Search is served from the graph on `:8020`; no request leaves the box for it.

## Verified 2026-08-16 - route camera coverage

Camera count now scales with the walk instead of sitting at three:

| route | cameras | span |
|---|---|---|
| 517 m | 12 | 0 - 516 m along |
| 1.07 km | 19 | 0 - 1070 m along |
| 1.4 km (in-app) | 28 | 0 - 1431 m along |
| 2.8 km | 36 | 0 - 2808 m along |

In the browser at 414x896, a 1.4 km route draws 28 camera markers along its whole length, the control badge reads 28, and the sheet reads "Cameras on your route · 28 watching your way · in passing order".
Ordering is strictly monotonic in `along_m`, and the furthest camera off the route measures 168 m against the 180 m corridor.
Cameras sitting on the route itself measure 1.8 m and 2.8 m off it.

Cost of the larger list: 28 tiles fetch **4** frame images, because tiles lazy-load; zero `<video>` elements exist until a camera is opened; and zero requests reach `streamlock.net`.
`FrameRecord` polling was moved to 60 s with a missing-only retry, so a 28-camera route no longer bursts the record endpoint.

Edge cases: a single-point polyline measures to the point (5 cameras), an empty polyline is a 400, malformed JSON is a 400, and a route through an area with no coverage returns 0 cameras with the panel saying that is a gap in the camera network rather than a judgement about the streets.
With media-ingest down and a warm cache the route layer keeps working from static camera positions; cold, it returns `{ok:false, why}` and routing is unaffected.

## Verified 2026-08-16 - cameras, against a running media-ingest

The gap this section used to record ("untested against a running media-ingest") is closed.

`ingest.graph` built 646 cameras, 355 with streams. Through walk-app on one port, at 414x896 in Chromium:

- `/api/cameras` returns 33 cameras within 500 m, nearest first, with real intersection names and distances. `grep -c streamlock` over the response is **0**.
- The full HLS chain was walked by hand through both proxies: `playlist.m3u8` -> `chunklist_w*.m3u8` -> a 2.6 MB `video/mp2t` segment, HTTP 200. Relative playlist URIs resolve back through the proxy as intended.
- Real frames render (720x480 JPEG from 2nd Ave & Spring St), with badges reading `CACHED 16m`, `SNAP 43s`, `SNAP 2m` - every one carrying a measured age.
- With the camera list open, `document.querySelectorAll('video').length === 0`: no tile streams. Opening a camera creates exactly one `<video>`, requesting `/api/hls/2_Spring/playlist.m3u8` on our own origin, with zero requests to `streamlock.net`.
- Graceful degradation was verified live: the SDOT stream host began refusing TCP 443 mid-session, and the viewer fell back to a real cached snapshot with an honest `CACHED 21m` badge instead of a dead black box.
- Location granted: title "Cameras near you", cameras re-query around the fix (5th & Union 118 m, nearest first), blue dot drawn, map eases to z15.5, and one tap produces a 1.1 km route starting from the walker.
- Location denied: title "Cameras downtown", the panel states the cameras are not near you, and the list still populates.

Two bugs were found and fixed by running it, neither visible from reading:

1. `canPlayType("application/vnd.apple.mpegurl")` returns `"maybe"` in Chromium, so the old code took the native-HLS branch and died with `DEMUXER_ERROR_COULD_NOT_PARSE`, never reaching hls.js. `Hls.isSupported()` is now the primary test, native the fallback.
2. The frame proxy's 8 s timeout aborted cold upstream fetches that take 8-10 s, so tiles rendered empty with a silent 503. Frame and HLS budgets are now 20 s, matching what media-ingest allows itself.

## Verified 2026-08-15

`bun run smoke` passes all 8 assertions. `bun run build` typechecks and builds clean.
In Chrome at 414×896: 2D and 3D both render, with 683 extruded buildings carrying real heights (79 m, 37 m, 30 m) in the 3D view; tap-to-route returns a real 1.1 km route; tapping the route opens the evidence sheet for `Western Avenue, at Madison Street`, correctly flagging lighting as not mapped in OpenStreetMap.
