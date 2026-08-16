# modules/harness — Deterministic Step (harness)

**Owner:** Ioli · **Effort budget:** 1–2 h (existing tools)

Read `../../SPEC.md` first. Lane: this directory only.

## Scope

All deterministic (non-ML) logic, shipped as a Python library that synthesis
imports (no service of its own):

- **Pathfinding A→B**: port safe-walk's street graph + risk-weighted A*
  (`experiments/safe-walk/safewalk/graph.py`, `routing.py`, `static_data.py`).
  Emits `Route` (§6.6) for both `shortest` and `safer`; risk weights come from
  `SegmentAssessment.risk` supplied by synthesis, falling back to safe-walk's
  static risk when no assessment exists yet.
- **Camera convergence**: street/area → ordered cameras. Port surukamera's
  camera↔street snapping (`experiments/surukamera/app/streets.py`) and manifest
  (`app/manifest.py`) — 472 snapped cameras, bearings + HLS URLs included.
  Emits `CameraConvergence` (§6.7).
- Shared geo utilities (segment ids, nearest-segment lookup for alerts).

**Out of scope:** ML of any kind, HTTP serving, fetching imagery (media-ingest
owns fetching; this module only knows camera *metadata*).

## Outputs (produced contracts)

`Route` (§6.6), `CameraConvergence` (§6.7). Keep `segment_id` stable
(`sw:<safe-walk edge id>`) — synthesis and the frontend key everything off it.

## Definition of done (demo)

`from harness import route, cameras_for` works from a fresh pull; a route across
downtown returns in <1 s with correct `cameras_en_route`; "Pike St" returns its
cameras with bearings and HLS URLs.

## Practices

- Pure functions over cached data files; build caches with a documented one-shot
  command (mirror safe-walk's `python -m safewalk.routing` pattern).
- Both experiments already pull OSM/SDOT — reuse their cached artifacts and
  fetch code rather than re-hitting Overpass during the hack.

## Status

Both `route()` and `cameras_for()` are implemented and verified against real
data (2026-08-15) — the module is feature-complete for its SPEC.md scope.
`route()` was ported from a personal prototype (own A* router + risk model,
distinct from `experiments/safe-walk`). `cameras_for()` was ported from
surukamera's camera manifest (`app/manifest.py`, 650 cameras) and street-snap
index (`app/streets.py` output, 472 snapped). `route()`'s `cameras_en_route`
is populated by sampling the path through `cameras_for()` — no longer a stub.

**Bearing caveat:** `bearing_deg` is the OSM road axis only, not a resolved
facing direction — surukamera's full bearing stack (`app/bearing.py`) needs
live snapshot fetches + a VLM to pick between the axis's two possible
directions, which is out of scope here ("no ML" per this file's Scope). So
`bearing_conf` is always 0.35 (the bearing stack's own "unresolved" tier) when
a bearing exists at all, never higher. See `harness/cameras.py`'s docstring
for how to raise it later without an interface change.

## Quickstart

```
cd modules/harness
python3 scripts/build-graph.py   # only if data/walk_graph.json is missing —
                                  # it ships pre-built (~11 MB, gitignored)
python3 -c "
import harness
print(harness.route((-122.3421, 47.6097), (-122.3301, 47.5983), 'safer'))
print(harness.cameras_for({'street': 'Pike St'}))
print(harness.cameras_for({'lat': 47.6097, 'lon': -122.3421, 'radius_m': 300}))
"
```

`from harness import route, cameras_for` — stdlib only, no dependencies to
install. `data/cameras.json` and `data/streets.json` ship pre-built (copied
from `experiments/surukamera/data/`, gitignored); refresh with:
```
cp ../../experiments/surukamera/data/cameras.json data/cameras.json
cp ../../experiments/surukamera/data/streets.json data/streets.json
```

- `route(origin, dest, kind)` — `origin`/`dest` are `(lon, lat)`; `kind` is
  `"shortest"` or `"safer"`. Returns the `Route` dict (§6.6) plus a non-contract
  `segments` list (per-segment `risk_parts`, geometry) that map-frontend uses
  for its evidence sheet until synthesis's `SegmentAssessment` (§6.4) replaces
  it. Raises `harness.RouteError` with a machine-readable code (`out_of_area`,
  `too_close`, `no_route`, `unknown_kind`, `detour_cap_exceeded`).
- `cameras_for(query)` — `query` is `{"street": <name>}` (forgiving substring
  match, e.g. `"pike"`) or `{"lat", "lon", "radius_m"}` (radius defaults to
  300 m). Returns `CameraConvergence` (§6.7).
