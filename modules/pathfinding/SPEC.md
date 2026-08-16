# modules/pathfinding — the ONE router that ships

**Owner:** Berkan · **Effort budget:** 3 h · Aug 16 sprint

Read `../../SPEC.md` §5.1 first (spike dispositions). This module is the single
source of truth for pathfinding. When it ships, the other routers are
deprecated-for-demo (their owners mark them so; nothing is deleted).

## Mission

One tool, one entrypoint: **(live location | point A) → point B**, deterministic
A*, with **every weight, heuristic, and piece of computed data we have**
participating natively in the search — not painted on afterwards.

Spikes 2 & 3 are closed: deterministic A* (no LLM routing — slower by orders of
magnitude, non-reproducible, unverifiable to a judge; our own §5 "No ML" rule
already forbids it), no new OSS engine (the differentiator is the weights, the
router is the boring solved part and stays boring).

## What exists today (consolidation inputs — port, don't rewrite)

| Piece | Where | What it contributes |
|---|---|---|
| A* + REAL SDOT static risk (collisions, sidewalk inventory, arterial class, slope) | `experiments/safe-walk/safewalk/routing.py`, `static_data.py` | the real static weight layers; measured 2.4 ms |
| A* over Overpass-built graph, per-segment `base_risk`/`risk_parts`, cameras_en_route, detour cap | `modules/harness/harness/routing.py` | segment sheet + en-route camera logic. **Its `_jitter()` collision/confidence/stale numbers are fabricated placeholders and MUST NOT ship** (already stripped by media-ingest when forwarding) |
| A* from committed OSM data (no network on fresh checkout), honest `inferred` markers, §6.4-shaped output | `modules/walk-app/server/routing.ts` | the committed-data graph build + honesty pattern for untagged inputs |
| Live evidence overlay: camera coverage + pixel activity, co-presence, lit+sun, open refuges → `live_risk` with a documented formula | `modules/media-ingest/ingest/pathrisk.py` → `GET :8030/api/path` | already running end-to-end (two clicks → risk-colored segments in the test console) |

## The consolidated design

1. **Graph**: committed-data build (walk-app's approach — a fresh checkout must
   route with zero network), extended to harness's bbox if time allows.
2. **Static weights**: safe-walk's real SDOT layers (collisions normalized by
   segment length only — no invented exposure model), OSM sidewalk/lit/class
   with walk-app's `inferred` honesty markers.
3. **Live weights, native**: media-ingest evidence (activity, co-presence,
   refuge-open-now, darkness) fetched per corridor and applied as edge-cost
   multipliers INSIDE the A* relaxation — risk is part of the search, exactly
   as "safer" already works, with the RISK_FORMULA documented in the response.
4. **Entrypoints**: Python lib call + `GET /api/route` (§6.6 shape + enriched
   segments as media-ingest's `/api/path` returns today). Live-location input
   is just origin=device GPS — no special path.

## Contracts

Produces `Route` §6.6 exactly; enriched segment fields are additive siblings
(same shape media-ingest `/api/path` serves today). `SegmentAssessment` §6.4
stays synthesis's — if synthesis lands, its per-segment `risk` REPLACES our
live formula (ours is the stopgap, labeled as such).

## Binding rules

- Deterministic: same inputs → same route, always.
- No fabricated numbers. Anything untagged/unmeasured is `null`/`inferred`,
  never a plausible-looking value. `_jitter()`-derived fields never ship.
- Every weight in the output carries its source (`sdot-collisions`, `osm-tag`,
  `pixel-activity`, `osm-opening-hours`, …).
- Router-consolidation decision needs Ioli + Dhruv sign-off at the table
  (walk-app's SPEC explicitly flags this as a team call).

## Definition of done

Two clicks (or live location) → route in <50 ms static + ≤1 s with live
evidence; all three legacy routers marked deprecated-for-demo by their owners;
demo-ui renders the output with zero adaptation.
