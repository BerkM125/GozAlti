# modules/map-frontend — Map Integration (front end)

**Owner:** Ioli · **Effort budget:** 2–3 h (ideally 4 for uniqueness) · **ESSENTIAL TO DEMO**

Read `../../SPEC.md` first. Lane: this directory only.

## Scope

The phone-usable map UI the demo runs on:

- Map of Seattle (MapLibre — harvest `experiments/surukamera/web/`, which already
  has MapLibre, camera markers, and confidence-scaled view cones; safe-walk's
  `web/index.html` shows the route/contact-sheet interaction model).
- A→B input → draw both `Route`s (shortest vs safer) with the
  `evidence_summary` visible.
- Click a street/segment → `CameraConvergence` → camera markers + preview panes
  (HLS `<video>` via hls.js where `live_hls` present, else snapshot `<img>` with
  fetch-time badge).
- Render `Observation` detections as dots/outlines on camera previews (use
  normalized `cx`/`cy`) and `SegmentAssessment` evidence as popovers on segments.
- Subscribe to `GET :8020/api/alerts/stream` (SSE) → banner + map pulse on
  `Alert`, with `severity` styling. This is the LIVE piece of the demo.

**Out of scope:** any routing/camera/safety logic (harness + synthesis own that);
any direct calls to SDOT — everything comes from synthesis (:8020).

## Inputs (consumed contracts)

`Route` (§6.6), `CameraConvergence` (§6.7), `SegmentAssessment` (§6.4),
`Observation` (§6.2, for overlays), `Alert` (§6.5, via SSE).

## Definition of done (demo)

Phone browser, walking outside: route shown, street click opens live camera,
evidence popover reads clean, alert banner fires when synthesis pushes one.
Works over the venue network on a phone screen size.

## Practices

- Zero-build or minimal-build (plain JS or Vite); dev server on :5173, static
  files served by synthesis in the demo so the phone hits one origin.
- Mobile first: big tap targets, readable outdoors (dark UI like surukamera's).
- Mock all five contracts with fixture JSON on day one so UI work never blocks
  on backend readiness; swap to live endpoints behind one config constant.

## Status

Ported from a personal prototype (React + Vite + MapLibre + protomaps, own A*
backend), adapted to the `Route` contract (§6.6): fixture, mock router, types,
and map layer all speak `kind`/`polyline`/`length_m`/`segment_ids`/
`cameras_en_route`/`evidence_summary` now instead of the old ad hoc shape.
`npm run build` and the dev server were verified 2026-08-15.

Covers: A→B tap-to-route, shortest vs safer polylines, detour-ratio banner,
tap-a-segment evidence sheet (risk bars, non-contract `segments` extension from
harness). Not yet covered (deferred, see harness's camera convergence stub):
camera markers/previews, VLM detection overlays, the alerts SSE banner.

## Quickstart

```
cd modules/map-frontend
npm install
npm run dev        # http://localhost:5173, USE_MOCK=true by default — no backend needed
```

Real basemap tiles/fonts/sprites are already checked out under `public/`
(gitignored, ~13 MB) so `npm run dev` renders the dark basemap immediately. If
they're ever missing, see `scripts/build-tiles.sh` and the fonts/sprites note
in the safe-walk prototype's own README.

`npm run build && npm run preview` builds and serves `dist/` — matches how
synthesis will serve it in the demo (same-origin, `API_BASE = ""`).

Flip `USE_MOCK` to `false` in `src/config.ts` once synthesis's route endpoint
exists; `src/api.ts` currently posts to a placeholder `/api/route` pending that
contract (SPEC.md §6.8 defines the port, not the route-fetch path/method yet —
coordinate with the synthesis owner before wiring this up for real).

### Phone demo over cloudflared (real routing, single origin)

`server/main.py` is a stopgap FastAPI app — same shape as the safe-walk
prototype's old server, updated to call `harness.route()` and serve exactly
the `Route` contract on `/api/route`. It also byte-serves `/tiles/*.pmtiles`
(pmtiles needs HTTP Range support, which bare `StaticFiles` doesn't give you)
and mounts `dist/` last so one origin covers everything. Retire this file once
synthesis owns port 8020 for real.

```
cd modules/map-frontend
python3 -m pip install -r server/requirements.txt
# in src/config.ts: set USE_MOCK = false
npm run build
uvicorn server.main:app --host 0.0.0.0 --port 8020
cloudflared tunnel --url http://localhost:8020
```
Open the printed `https://<name>.trycloudflare.com` URL on the phone → Share →
Add to Home Screen. Turn the venue wifi off, hotspot on — labels, sprites,
tiles, and routing must all survive on one origin, no other network calls.

If `modules/harness/data/walk_graph.json` is ever missing, `/api/route` falls
back to `src/fixtures/route.json` rather than failing to boot — same fallback
behavior as the old server.

Verified locally 2026-08-15: `/health`, `/api/route` (both `shortest` and
`safer`, real graph), and 206 partial-content tile serving all work.
