# Live routing + 3D CV objects + router heatmap (16 Aug, ~03:20)

Decision log for the vision integration port (demo-ui rows 3 live-half and 7, plus the dual heatmap).
Written for the next session; keep entries short and dated.

## What landed

- Live PathLiveSession polling (the missing half of demo-ui row 3).
- Local-CNN cars/people as 3D extrusion meshes on the map (row 7), ported from `experiments/berkan_testing/app.js` (read-only reference).
- A second heatmap fed from the consolidated router's static weights, with the OSM layer as the always-working base.
- Bring-up of the router stack on this box (see Bring-up below).

## Decisions

**Methodology: hybrid as built (user decision).**
Instant deterministic route (~50 ms, cached CV shipped in the PathObject), then the router's live session analyzes only the corridor cameras on demand and auto-replaces the path.
No citywide VLM sweep; the 60 s/camera rate limit makes it infeasible and the static heatmap already covers the citywide picture.

**Live poll: safer only, keyed on path_id.**
`fetchOnePath` sends `live=true` only for `kind=safer`; the shortest trip is a static baseline.
One `setInterval(2000)` effect in App.tsx keyed on `pathPair.safer.path_id`; a version bump swaps in the whole new PathObject (auto-replace, per the router's 16 Aug design).
404 = session expired upstream (180 s TTL): stop polling, keep last state.
Transient errors skip the tick, never tear the route down.
Cleanup sends a fire-and-forget DELETE; the server TTL is the real backstop.
MapView only re-fits the camera when `path_id` changes, so version bumps never yank the view (`fittedTrip` ref).

**CV objects: one GeoJSON fill-extrusion source, per-camera upsert.**
`src/cvObjects.ts` composes each detection from a few extrusions (person = red octagon body + head; car = 4 wheel rects + yellow body + cabin, oriented to the CNN bearing) - no model files, ports anywhere MapLibre runs.
The footprint stays the CNN's estimate; only the visual shape is styled.
Detections without `est` (no camera bearing) or `footprint_m` are never drawn - no invented positions.
Store keyed by camera_id, upsert-only; bulk-wiped only when the trip is cleared, so objects never blink between passes.

**Three CV feeds, mirroring the console:**
1. `cv_detections` shipped with every PathObject render instantly (cached, age honest via `frame_ts`); re-ingested on every version bump.
2. One HQ detlib still pass per NEW `path_id` (not per version bump - the session refreshes corridor CV upstream): max 4 in flight, token-cancelled, one plain-yolo retry per camera when detlib is down.
3. The opened camera polls the cached fast lane at 350 ms (rate-limit-proof by server design), rebuilds only on `frame_ts` change, and auto-tilts to 3D once on the first placed detection (ref-guarded; the user's toggle wins afterwards).

**Heatmap: both sources (user decision).**
The in-process OSM-weights blocks layer stays the default and the fallback.
`GET /api/blocks/router` reads `modules/harness/data/walk_graph.json` + `modules/pathfinding/data/edge_static.json` off disk (a data read, not a code dependency, so the lane rule holds) and serves collisions + osint per edge - exactly the layers the OSM heatmap lacks.
Camera coverage is excluded: it is ~1.0 over most of the city and would wash the map.
Colors are rank-scaled onto the existing ramp because SDOT collision density saturates downtown; the legend stays a relative lower/higher.
Missing/misaligned artifacts answer 503 and the layers button just cycles two states instead of three.

**Osint scraped weights: included, for free.**
`modules/osint/data/signals.latest.json` (13 SPD-derived §6.3 AreaSignals) landed 16 Aug; `pathfind.build_static` reads it automatically.
Rebuilt overlay: 162,496 edges, 9,708 SDOT points, 13 osint areas, `pending: []`.

## Bring-up (this box, repeat after a fresh checkout)

```bash
cd modules/harness && python3 scripts/build-graph.py          # Overpass, ~1 min
cd modules/pathfinding && python -m pathfind.build_static     # SDOT+osint, ~7 s, AFTER graph
cd modules/media-ingest && ./.venv/bin/python3 -m ingest.setup_cv
./.venv/bin/uvicorn ingest.service:app --port 8030            # restart to mount /api/route
curl -X POST localhost:8030/api/sweep/start
```

Gotcha hit tonight: `~/.cache/torch` was root-owned (old sudo run), which broke detlib's checkpoint download with `Permission denied`.
Fixed by renaming it aside and letting torch recreate it; `TORCH_HOME` did NOT reach the CV worker processes.

## Verified (16 Aug ~03:15, browser E2E on :5173)

- Route A→B: consolidated router path, bucket-colored segments, evidence summary, 10 cameras en route.
- Live chip flipped `live pending` → `live v7 · opencv` (title: "deterministic + live overlay in-search. Cameras reporting: 9."), reached v20 as the session kept replacing the path; no camera yank on bumps.
- CV: 225+ mesh features (car/person/bus/truck/motorbike) after the still pass; screenshots in the session scratchpad.
- detlib runs on CUDA (~1.3 s/frame); empty frames return zero detections (honest, no fabrication).
- Clear route: objects, chip, and session all cleared.
- Router heatmap: 90,832 evidence edges, discriminates citywide; downtown saturation is real data, not a ramp bug.

## Round 2 (16 Aug ~04:15) - oscillation, visibility, warm-up

**Route oscillation fixed in `pathfind/live.py` (cross-module, cleared by Dhruv).**
Root cause: occupancy was rebuilt each tick from only the CURRENT corridor, and un-evidenced edges cost 0 while an evidenced empty street costs the 0.5 middle - so the shown route was penalized by its own cameras, every alternative rode free, and the optimum flipped endlessly (observed to v49).
Fix: evidence persists per session (120 s TTL, 20 s hold on the higher people count against CNN flicker), previously-seen cameras stay hot, and a challenger polyline must beat the incumbent by a 5% cost margin under the SAME evidence for two consecutive ticks.
Measured: 6 geometry flips/100 s before the margin, 0 flips/120 s after.
If a demo route ever seems stuck, the margin is `SWITCH_MARGIN` in live.py - it is stickiness by design, not a bug.

**CV visibility: dots in 2D, meshes in 3D.**
The meshes were always rendering, but a 4.5 m car viewed top-down at route zoom is a few pixels - invisible.
Every placed detection now also emits a Point feature; a circle layer (yellow vehicle / red person, white stroke) shows in 2D and hides in 3D where the meshes take over.
The dock adds "Cameras see N people · M vehicles on this route" so the evidence is visible without hunting.
Night frames genuinely produce fewer detections; zero detections on an empty street is correct output, not a bug.

**No new vision cache - warm up instead (user decision).**
media-ingest already caches CV per frame with a hot-camera prefetcher; a road-keyed cache on top would only add staleness risk and cannot beat the 60 s/camera upstream floor.
Pre-demo warm-up: run the demo route once ~2 minutes before filming (the route marks its corridor hot), or `POST :8030/api/priority` with the corridor camera ids.
First on-camera interactions during the take then answer from cache instantly.

- Bearing calibration: fresh `ingest.graph` builds leave `bearing_deg` null, so FOV cones and some CV placements stay hidden until `POST /api/orient/{cid}` runs (unchanged from before this port).
- LLM segment summaries (`:8030/api/path/summaries`): no proxy or consumer here yet; stretch.
- demo-ui rows 1 (all-cameras layer) and 2 (HLS outside the nearby merge): not part of this port.
