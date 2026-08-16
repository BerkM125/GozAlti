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
