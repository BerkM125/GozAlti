# GozAlti — God Spec

**GozAlti** (Turkish *göz altı* — "under the eye"): Seattle's public traffic cameras,
watched by AI, routing people around what they see — live, on a phone, on a DGX Spark.

This file is the single source of truth for the whole repository. Every human and
every Claude (Fable/Opus) session working in this repo reads this file first, then
the `SPEC.md` of the module it is assigned to, and stays inside that module's lane.
`CLAUDE.md` enforces this.

---

## 1. Mission

Build a live pedestrian-safety system for Seattle:

- **Pathfinding** from point A to point B, with a shortest route and an
  evidence-backed safer route.
- **Camera convergence**: pick any street or area → the public cameras that see it,
  with live video or snapshots.
- **Safety assessment** of streets/regions from three evidence sources: city static
  data (collisions, sidewalks), live camera footage read by a VLM, and public
  internet sentiment (Reddit, news, Seattle PD).
- **Live capability**: while a user walks with the phone app, the system pushes
  updates and warnings in case of danger.

Core principle (inherited from safe-walk, non-negotiable): **never an unexplained
"safety score."** Every claim shown to a user or a judge is a checkable number with
its evidence attached. The VLM reports what it sees (people, obstructions, lighting);
it never issues danger/crime verdicts — synthesis combines evidence into assessments.

## 2. Hackathon context — NVIDIA Spark Hack: Seattle

- **Event**: NVIDIA Spark Hack, thinkspace Seattle, Aug 14–16 2026.
  Submissions + demo fair: **Sunday Aug 16, by 4:00 PM PDT**. That is the deadline.
- **Track**: **See** — "AI that understands the physical world." Perception-first:
  cameras, video streams, images, maps. The See track expects teams to work with
  **NVIDIA VSS (Video Search and Summarization)** skills — the VLM module must use
  or credibly interoperate with the VSS Blueprint on the Spark.
- **Hardware**: one **Acer Veriton GN100** (NVIDIA GB10 Grace Blackwell Superchip,
  "DGX Spark") per team. Heavy inference runs there; Dhruv exposes model endpoints
  on the box for everyone else to call.
- **Data**: any public open data; City of Seattle open data portal is the anchor
  (SDOT cameras, collisions, sidewalks). Respect upstream rate limits — see §7.
- **Extra models on site**: Nemotron Lightning and Meta Glimmer available from
  hard drives — candidates for the VLM/synthesis steps without long downloads.

## 3. Demo — definition of done (the north star)

The demo is a team member **walking down a real street with the phone running the
app**, filmed, while the app simultaneously:

1. Shows a route (A → B) on the map, shortest vs safer, with the evidence shown.
2. Lets the presenter **click any street** to open the cameras that see it
   (live HLS where available, snapshots otherwise).
3. Shows **safety assessments** on segments/areas with evidence popovers,
   including VLM-detected people/hazards rendered as dots/outlines on the map.
4. Pushes a **live warning** when the situation on an en-route camera changes.

Any work that does not move one of those four numbers is out of scope until they
all work end-to-end. Integrate early: a thin walking skeleton across all modules
beats four polished islands.

## 4. Repository layout

```
GozAlti/
├── SPEC.md                  ← this file (god spec)
├── CLAUDE.md                ← session rules for Claude — read + obey
├── README.md
├── experiments/             ← BASE LAYERS, read-only (see §8)
│   ├── safe-walk/           ← routing, scraping, VLM captioning, risk model, API
│   └── surukamera/          ← camera↔street index, HLS discovery, bearings, geometry
├── modules/                 ← THE PRODUCT — one module per row of the team table
│   ├── map-frontend/
│   ├── harness/
│   ├── vlm/
│   ├── media-ingest/
│   ├── osint/
│   ├── synthesis/
│   ├── walk-app/            ← mobile PWA shell (Aug 16)
│   ├── pathfinding/         ← the ONE router that ships (Aug 16 sprint)
│   ├── demo-ui/             ← final-UI reconciliation checklist (Aug 16 sprint)
│   ├── ios-pwa/             ← iOS PWA constraints + on-device test (Aug 16 sprint)
│   ├── audio-lm/            ← voice companion STT/TTS loop (Aug 16 sprint)
│   ├── offpath-911/         ← off-path prompt + gated escalation (Aug 16 sprint)
│   └── calling/             ← optional outbound-call feature, cut first
└── demo/                    ← demo script, video assets, run-of-show
```

Each module directory contains a `SPEC.md` (scope, contracts, definition of done).
Module owners create their own internal structure; nobody else edits inside a
module they don't own without the owner's sign-off (pairing at the same table
counts).

## 5. Module registry (one row = one module)

| Module dir | Table row | Owner | Effort | One-liner |
|---|---|---|---|---|
| `modules/map-frontend` | Map Integration (front end) — **ESSENTIAL TO DEMO** | Ioli | 2–3 (ideally 4) | Map UI: routes, camera locations, camera previews, path analysis overlays (dots/outlines of people & hazards). |
| `modules/harness` | Deterministic Step (harness) | Ioli | 1–2 | Deterministic logic: pathfinding, street/area → local cameras (camera convergence). No ML. |
| `modules/vlm` | VLM Step — **model's output** | Adi (endpoints: Dhruv) | 4 | VLM analysis of footage on the Spark via VSS: detect people/hazards, structured output with pointable coordinates. |
| `modules/media-ingest` | Media Data Scraping/Analysis — **model's input** | Berkan | 3 | Serve camera frames/clips TO the VLM — plus the **offline camera-context surface** (camera graph, street↔camera maps, bearings, activity flags, open-business hours, street facts, satellite/buildings). See §6.9 and `modules/media-ingest/README.md` before fetching any camera/street/OSM data yourself. |
| `modules/osint` | Non-Media Scraping (optional) — **model's input** | Dhruv | 3 | Scrape Reddit/news/Seattle PD for area-level safety sentiment from past events and anecdotes. |
| `modules/synthesis` | Data Synthesis — **model's output** | All (downstream) | 3 | Combine media evidence + VLM reads + osint sentiment into final per-segment assessments and live alerts. |
| `demo/` | Demo/Video — **ESSENTIAL TO DEMO** | Min. 3 people | — | Demo script, filming plan, live run-of-show, fallback recordings. |
| `modules/walk-app` | Mobile PWA walking app | Dhruv | 3 | React/Bun/Vite PWA: glass UI, in-process pedestrian routing from committed OSM data, media-ingest proxy. Carries one of the repo's three routers — see `modules/pathfinding`. |
| `modules/pathfinding` | **Final pathfinding tool** (Aug 16 sprint) | Berkan | 3 | ONE shipped router: live-location-or-point → point, deterministic A*, every real weight + live camera evidence natively in the search. Consolidates the three existing routers; spikes 2 & 3 are CLOSED (deterministic A*; no LLM routing; no new OSS engine). |
| `modules/demo-ui` | **Final demo UI** (Aug 16 sprint) — **ESSENTIAL TO DEMO** | All (UI leads: Dhruv + Ioli) | 4 | Single source of truth for what the ONE demo app must render: the feature checklist reconciling walk-app, map-frontend, and everything proven in the experimental console. |
| `modules/ios-pwa` | iOS PWA constraints (was "iOS port") | Adi | 1 | There is no native port — walk-app is a PWA. This documents iOS Safari limits (secure-context geolocation, no background location, push ≥16.4+homescreen) and owns the 20-minute on-device test. |
| `modules/audio-lm` | Audio LM companion | Adi | 3 | Open-weight STT (Whisper-class) + TTS conversation loop on the Spark. Confirmation-gated; feeds `modules/offpath-911`. |
| `modules/offpath-911` | Sudden-turn / off-path emergency prompt | Berkan | 2 | Deterministic off-path detection (distance from route polyline over time) → audio prompt → explicit-confirmation escalation. **Never dials real 911 in dev or demo.** |
| `modules/calling` | Outbound call to a contact — **OPTIONAL, cut first** | unassigned | 1 | AI places a call to a designated contact (never emergency services) with evidence-backed location/situation. Only if everything else is done. |

Effort unit: 1 = 1 hour. Treat it as a budget, not an estimate — when a module
exceeds its budget, cut scope inside the module rather than borrowing time from
integration.

### 5.1 Aug 16 sprint plan (spike dispositions + order of attack)

Spike results (full write-up circulated 16 Aug; verified against the repo):

- **Spike 1 (iOS vs Android): closed — reframed.** walk-app is a PWA; the real
  risk is iOS Safari blocking `navigator.geolocation` over plain-HTTP LAN/tailnet
  IPs (localhost is exempt). `modules/ios-pwa` owns the binary on-device test +
  mitigations (mkcert / `tailscale cert` / laptop fallback).
- **Spikes 2 & 3 (pathfinding algorithm / OSS offload): closed.** Deterministic
  A* — already built three times, measured at ms-scale; §5 harness row already
  says "No ML" and that stands. No LLM routing (slow, non-reproducible,
  unverifiable); no new engine (Valhalla/GraphHopper/pgRouting are data-pipeline
  rewrites, not spikes). The differentiator is the WEIGHTS, not the router.
- **Spike 4 (which router ships): the consolidated router LANDED 16 Aug
  ~01:45** — `modules/pathfinding`, served at `GET :8030/api/route` (+
  `/api/route/live/{path_id}` for versioned auto-replacing live updates).
  Real SDOT collisions (9.7k records) + camera coverage + OSM structure +
  osint hook in the static weights; live OpenCV/VLM occupancy + flags enter
  the A* natively via the session loop. No `_jitter()` anywhere in it.
  Remaining table item: Ioli (harness) + Dhruv (walk-app) mark their routers
  deprecated-for-demo and demo-ui consumes `/api/route`.
- **Spike 5 (synthesis is empty): CRITICAL PATH.** It owns :8020 and the
  flags→weight table (design already written in `modules/vlm/CAPABILITIES.md`;
  port capped-nudge logic from `experiments/safe-walk/live.py`). Unblocks
  map-frontend off its mocks. Highest-leverage work in the repo.
- **Spike 6 (nothing validates obstruction detection):** watch the ~27
  construction-flagged cameras, label with `modules/vlm/lab/label.py`, done at
  ≥10 confirmed positives + a measured miss rate.

Order of attack: synthesis (spike 5) → router decision (spike 4) → device test
(spike 1) → Adi: `ios-pwa` + `audio-lm`; Berkan: `pathfinding` then
`offpath-911` → all hands on `demo-ui` polish → demo plan + recording, on the
clock in §5.2. `modules/calling` only if everything else is done.

### 5.2 HARD DEADLINES — Sunday Aug 16, PDT (set 00:34 AM)

Binding for **every dev and every Claude/agent session in this repo**. When a
deadline is at risk, cut scope inside the item — never slide the clock. Each
line names the modules on the hook. "New UI" = the demo shell per
`modules/demo-ui` (walk-app pending sign-off); the experimental console
(`experiments/berkan_testing`) is the porting reference, not a deliverable.

| Deadline | Deliverable | Modules on the hook |
|---|---|---|
| **1:00 AM** | All currently-known UI bugs fixed; every feature already proven on the experimental console ported to the new UI (demo-ui checklist rows 1–2, 6–9: cameras + activity brightness, live feeds, refuge exits, local-CV objects, FOV cones, context panels) | `demo-ui` (all hands) |
| **1:30 AM** | Pathfinding fully working ON the new UI — not yet done on either UI: en-route cameras highlighted correctly, risk accuracy sanity-evaluated against a few known streets, and the route ingesting **all** of it: VLM observations, Dhruv's scraped osint signals, camera/graph data, geolocation, live OpenCV detections | `pathfinding`, `demo-ui`, feeds from `vlm`/`osint`/`media-ingest` |
| **2:00 AM** | Simple "911 calling" orchestrated in the app — simulated endpoint / designated teammate number ONLY, per the hard safety rules in `modules/offpath-911` (real 911 is never dialed, dev or demo) | `offpath-911`, `calling` (if used), `demo-ui` |
| **2:30 AM** | Both triggers implemented: spoken keyword (via `audio-lm`) AND sudden-turn/off-path drift each raise the confirmation-gated emergency prompt | `offpath-911`, `audio-lm` |
| **2:45 AM** | Demonstration plan written (run-of-show in `demo/`) | `demo/` (all hands) |
| **3:00 AM** | Demo done entirely — recorded, fallback footage saved | `demo/` (all hands) |
| **3:00–4:00 AM** | Wiggle room: fix fallout from the above, then **submit fully** | everyone |

Session rule: an agent session picking up work after any of these times treats
the earlier items as frozen — bug fixes only, no new scope in a slot whose
deadline has passed.

## 6. Data contracts

Modules talk **only** through these shapes. Changing a contract requires updating
this section in the same commit and telling the affected owners — a Claude session
must never silently change a field another module consumes. All timestamps ISO-8601
UTC. All coordinates WGS84 `lat`/`lon`. Normalized image coordinates `cx`/`cy` in
`[0,1]` from top-left.

### 6.1 `FrameRecord` — media-ingest → vlm
```json
{
  "camera_id": "sdot:1_NW",
  "captured_at": "2026-08-15T22:10:00Z",
  "lat": 47.6097, "lon": -122.3331,
  "kind": "frame",                  // "frame" | "clip"
  "path": "data/frames/sdot:1_NW/20260815T221000Z.jpg",
  "source": "sdot-snapshot",        // "sdot-snapshot" | "sdot-hls" | "phone"
  "stale": false                    // true if dead-camera placeholder detected
}
```

### 6.2 `Observation` — vlm → synthesis (and frontend overlays)
```json
{
  "camera_id": "sdot:1_NW",
  "frame_ts": "2026-08-15T22:10:00Z",
  "read_at": "2026-08-15T22:10:09Z",
  "model": "vss/qwen2.5-vl",
  "people_count": 3,
  "detections": [
    { "label": "person", "cx": 0.42, "cy": 0.61, "conf": 0.83 }
  ],
  "flags": ["blocked_sidewalk"],    // closed enum, defined in modules/vlm/SPEC.md
  "caption": "three pedestrians on the north sidewalk; construction barrier ..."
}
```
The VLM emits observations only — no verdicts. Strict JSON schema enforced at the
endpoint (retry on schema miss).

### 6.3 `AreaSignal` — osint → synthesis
```json
{
  "area": "belltown",
  "centroid": { "lat": 47.6141, "lon": -122.3459 },
  "source": "reddit",               // "reddit" | "news" | "spd" | "other"
  "url": "https://...",
  "observed_at": "2026-08-14T03:00:00Z",
  "sentiment": -0.4,                // [-1, 1]
  "summary": "two posts describing harassment near 3rd & Bell last week"
}
```

### 6.4 `SegmentAssessment` — synthesis → harness + map-frontend
```json
{
  "segment_id": "sw:12345",         // safe-walk street-graph segment id
  "risk": 0.37,                     // relative weight for routing, not a user-facing score
  "evidence": [
    { "type": "collision", "ref": "sdot-collision:...", "detail": "2 ped collisions since 2020" },
    { "type": "vlm",       "ref": "sdot:1_NW@2026-08-15T22:10:00Z", "detail": "sidewalk blocked" },
    { "type": "osint",     "ref": "https://...", "detail": "negative sentiment, 2 reports" }
  ],
  "updated_at": "2026-08-15T22:10:30Z"
}
```
Everything the UI shows a user comes from `evidence[]`, never from bare `risk`.

### 6.5 `Alert` — synthesis → map-frontend (live push)
```json
{
  "id": "alert-0042",
  "severity": "caution",            // "info" | "caution" | "danger"
  "lat": 47.6103, "lon": -122.3340,
  "segment_id": "sw:12345",
  "message": "Camera at 4th & Pike now shows the west sidewalk blocked",
  "evidence": [ { "type": "vlm", "ref": "sdot:...", "detail": "..." } ],
  "issued_at": "2026-08-15T22:11:02Z"
}
```

### 6.6 `Route` — harness → map-frontend
```json
{
  "kind": "safer",                  // "shortest" | "safer"
  "polyline": [[47.6097, -122.3331], [47.6099, -122.3325]],
  "length_m": 1240,
  "segment_ids": ["sw:12345", "sw:12346"],
  "cameras_en_route": ["sdot:1_NW", "sdot:44_SE"],
  "evidence_summary": "avoids 3 segments with recorded ped collisions; +140 m"
}
```

### 6.7 `CameraConvergence` — harness → map-frontend
```json
{
  "query": { "street": "Pike St" },   // or { "lat": ..., "lon": ..., "radius_m": 300 }
  "cameras": [
    {
      "camera_id": "sdot:1_NW",
      "lat": 47.6097, "lon": -122.3331,
      "bearing_deg": 315, "bearing_conf": 0.8,
      "live_hls": "https://61e0c5d388c2e.streamlock.net/.../playlist.m3u8",
      "snapshot_url": "https://..."
    }
  ]
}
```
`live_hls` is `null` for snapshot-only cameras. Bearings come from surukamera's
bearing stack; `bearing_conf` drives view-cone opacity in the UI.

### 6.8 Transport & port registry

| Service | Owner | Port | Notes |
|---|---|---|---|
| synthesis API + alert stream | All | **8020** | REST + SSE `/api/alerts/stream`; the frontend's single backend |
| media-ingest service | Berkan | **8030** | feeds vlm; internal |
| vlm endpoints (on the Spark) | Dhruv/Adi | **8040** | VSS-backed; internal |
| map-frontend dev server | Ioli | **5173** | static build served by synthesis in the demo |
| experiments/safe-walk (reference) | — | 8010 | leave as documented |
| experiments/surukamera (reference) | — | 8000 | leave as documented |

Harness ships as a Python library imported by synthesis (no port of its own).
Live push is **SSE** (works over plain HTTP on a phone; no websocket infra needed).

### 6.9 media-ingest context surface (`:8030`) — use it, don't re-fetch

media-ingest runs **on the DGX Spark itself**, next to the vlm module, so every
agent and module on the box has **instant local access** to its artifacts and
endpoints. Binding rule for all sessions: **before fetching any camera, street,
business-hours, building, satellite, or OSM data from the internet, check
`modules/media-ingest/README.md` — it is almost certainly already served
offline on `:8030`.** Duplicating those fetches wastes demo-critical time and
risks upstream rate-limit bans (§7.5). Full endpoint + artifact reference:
`modules/media-ingest/README.md`. Highlights:

- **Offline, answers in milliseconds** (saved artifacts / pure computation):
  camera graph queries (`/api/nearby`, `/api/convergence` §6.7, `/api/streets`,
  `/api/street/{name}`), city-wide pixel-activity map (`/api/activity` —
  which cameras show motion right now; never a people/safety claim),
  open-businesses-near-a-point with live open/closed evaluation
  (`/api/refuge…`), structural street facts (sidewalk/lit/alley/crossings on
  every node), sun position (`/api/sun`), last detections
  (`/api/detections`), Observation breadcrumbs (`/api/observations/{cid}`),
  cached tiles/satellite/building footprints, and
  **`GET /api/context/{cid}` — everything known about one camera in a single
  document, the first stop when assembling VLM context.**
- **Local CNN detection (no VLM, no internet at inference time)**:
  `GET /api/cv/camera/{cid}` and `GET /api/cv/point?lat=&lon=` run
  YOLOv4-tiny in parallel local worker processes on the freshest frames and
  return cars/people with estimated lat/lon world positions
  (`pos_conf` + basis attached; per-frame result cache makes polling
  rate-limit-proof). One-time install on any box:
  **`python -m ingest.setup_cv`** (pip deps + ~24 MB open-source model +
  smoke test). Use this for cheap what-lies-ahead object context;
  safety interpretation still belongs to vlm/synthesis.
- **Online but rate-gate-owned by media-ingest**: fresh snapshots
  (`/api/frame/{cid}/latest.jpg`), live HLS proxy (`/api/hls/{key}/…`).
  **No other module talks to SDOT/Wowza/Overpass/Esri directly.**
- Integration hooks: `POST /api/priority` (synthesis marks en-route cameras
  hot), `POST /api/read/{cid}` (hot-lane push to `:8040/read` with
  `prior_observations` as a sibling key next to the untouched §6.1 record —
  pending vlm-owner confirmation), `SYNTH_OBS_URL` (auto-forward of
  Observations to synthesis).
- Fresh clone / Spark setup: `data/` artifacts are gitignored — run the
  one-time builders in the README (graph build needs no network; refuge +
  statics each cache a single Overpass pull; `ingest.setup_cv` installs the
  local CNN once).

## 7. Engineering practices (all modules, all sessions)

1. **Read before writing.** SPEC.md → your module's SPEC.md → the experiment code
   you are harvesting. The experiments already solve camera inventory, HLS
   discovery, scraping discipline, street graphs, routing, VLM schemas, bearings.
   Port, don't reinvent.
2. **Stay in your lane.** Edit only your module (plus `demo/` and contract sections
   here with owner sign-off). Cross-module needs → change the contract, not the
   other module's code.
3. **Small, honest commits.** Working state on `main` at all times; a broken `main`
   during a 48-hour hack costs everyone. Branch per module (`mod/vlm`, `mod/ingest`,
   …) if you need to park broken work; merge only green. Commit messages say what
   changed and which contract it touches.
4. **Contracts are law.** Emit and accept exactly the shapes in §6. Validate at
   boundaries (pydantic / JSON schema); fail loudly on schema misses.
5. **Rate-limit discipline** (inherited from both experiments): ≥60 s between
   snapshot fetches per camera, ≤4 concurrent upstream requests, descriptive
   User-Agent. Getting the team IP-banned mid-hack is a project-ending failure.
6. **Secrets** live in `.env` (gitignored) and are read via config modules — never
   hard-coded, never committed.
7. **Heavy data** (frames, caches, model weights) is gitignored and rebuildable
   via documented commands. Exception: `experiments/surukamera/data/` stays as
   shipped so its demo runs offline.
8. **Runnable in one block.** Every module's SPEC.md keeps a copy-pasteable
   quickstart current. If a teammate can't run your module from a fresh pull in
   5 minutes, it isn't done.
9. **Demo first.** When choosing between a feature and demo reliability, choose
   reliability. Record fallback footage of anything that works — live demos fail.
10. **Honest claims.** No fabricated numbers, no invented "safety scores," no
    VLM danger verdicts. This is both ethics and our differentiator to judges.

## 8. `experiments/` — base-layer rules

`experiments/safe-walk` and `experiments/surukamera` are the two iterated
prototypes this project grows from. They are **read-only reference layers**:

- **Never modify them.** Harvest by copying code *into* your module and adapting
  it there. If you find a bug in an experiment, note it in your module's SPEC.md
  and fix the ported copy.
- What each one gives you:
  - **safe-walk**: SDOT camera inventory (`safewalk/cameras.py`), sweep scraper
    with dead-camera detection (`scraper.py`), VLM prompt + strict schema
    (`vision.py`), street graph + collision/sidewalk joins (`graph.py`),
    risk-weighted A* routing (`routing.py`), live nudges (`live.py`), FastAPI
    surface (`api.py`), SVG map UI (`web/`).
  - **surukamera**: canonical camera manifest + HLS stream URL recovery
    (`app/manifest.py`, `docs/ENDPOINTS.md`), street↔camera snapping and
    ordering (`app/streets.py`), per-frame geometry + PTZ drift handling
    (`app/geometry.py`), the 7-layer bearing stack (`app/bearing.py`), MapLibre
    UI with view cones (`web/`), the DNS-over-TCP netboot proxy for hostile
    networks (`app/netboot.py`).

## 9. Working with Claude in this repo

Sessions are expected to be Fable 5 or Opus. `CLAUDE.md` binds every session to
this spec. When you start a session, tell it which module you own; it must refuse
scope creep into other modules and must surface contract changes to you instead
of making them silently. Keep sessions module-scoped — one session, one module.
