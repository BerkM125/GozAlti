# modules/demo-ui — the ONE app the demo runs on

**Owners:** All (UI leads: Dhruv + Ioli) · **Effort budget:** 4 h · **ESSENTIAL TO DEMO**

Single source of truth for what the final demo app must render. This module is
a checklist + reconciliation spec, not a fourth codebase: the shell is decided
below and features are ported INTO it from where they already work.

## Shell decision (needs Dhruv + Ioli sign-off at the table)

**Recommendation: `modules/walk-app` (Dhruv's PWA) is the demo shell** — it is
mobile-first, routes from committed data on a fresh checkout, and already
proxies media-ingest. `modules/map-frontend` stays the desktop/backup surface
(it already renders real harness routes + the pmtiles basemap). The
experimental console (`experiments/berkan_testing`, uncommitted) is the
reference implementation to port features FROM — it is not the demo app.

## Feature checklist — "every single thing we need"

Each row exists and works somewhere today; the work is porting, not inventing.

| # | Feature | Proven where | Backing endpoint |
|---|---|---|---|
| 1 | Map + all 646 cameras, dot brightness = pixel activity | berkan_testing | `:8030/api/cameras`, `/api/activity` |
| 2 | Live HLS video + snapshot fallback per camera | berkan_testing, walk-app | `:8030/api/hls/…`, `/api/frame/…` |
| 3 | Two-point (or live-location) routing | berkan_testing PATH mode, walk-app, map-frontend | `:8030/api/path` → `modules/pathfinding` when it lands |
| 4 | **Risk-colored path segments** (green/amber/red) with per-segment evidence popover | berkan_testing | `/api/path` segments: `live_risk`, `risk_bucket`, `evidence`, `risk_basis` |
| 5 | **En-route cameras** highlighted + tappable | berkan_testing, harness `cameras_en_route` | `/api/path` `cameras_en_route_detail` |
| 6 | **Open "exit route" businesses** along the path (green dots, open-until) | berkan_testing | `/api/path` `refuges_en_route`; `/api/refuge…` |
| 7 | Local-CV 3D objects (cars/people) around a focused camera, fast lane + HQ detlib pass | berkan_testing | `/api/cv/camera/{cid}`, `?backend=detlib` |
| 8 | FOV cones + bearing state per camera | berkan_testing | camera responses `bearing` |
| 9 | Street context + refuge summary panels (structural facts, honest unknowns) | berkan_testing | `/api/context/{cid}` |
| 10 | Live alerts banner (SSE) | — blocked on synthesis (§5.1 spike 5) | `:8020/api/alerts/stream` when it exists |
| 11 | Live user location on the map | walk-app | device GPS — **secure-context rule below** |

## Hard constraint from `modules/ios-pwa`

`navigator.geolocation` fails silently on iOS Safari over plain-HTTP LAN/tailnet
IPs. The demo phone must load the app via HTTPS (mkcert / `tailscale cert`) or
localhost, and this must be verified ON the demo phone before feature work is
declared done. No background geolocation on iOS — the demo keeps the screen on.

## Binding rules

- Every rendered number/claim carries its basis (activity = pixel change only;
  refuge counts scoped to known OSM hours; risk colors link to `risk_basis`).
- Nothing renders from fabricated data — if a backend is absent the UI shows
  an explicit `not running` state (walk-app already does this; keep it).
- Lane respect: porting features into the shell touches only the shell's
  module + this spec.

## Definition of done

The Sunday run-of-show (walk + route + camera click + live warning) executes
start-to-finish on the demo phone from a fresh checkout with rows 1–9 live,
row 10 wired the moment synthesis exists, and zero mock data on screen.
