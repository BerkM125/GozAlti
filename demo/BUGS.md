# Bug list

Open defects across modules, most demo-visible first. Add to this rather than keeping
bugs in chat — several of these were found twice by different people.

Format: **severity** · file:line · what it does now → what it should do · owner

Severity: **BLOCKER** breaks the demo · **IMPORTANT** wrong output or unguarded boundary ·
**MINOR** correctness risk that has not bitten yet · **INFO** worth knowing, no action

---

## Open

### 1. IMPORTANT — route shows only ~3 cameras (single-point sampling)
`modules/walk-app/server/index.ts:157` · owner: walk-app

```ts
const mid = result.safer.polyline[Math.floor(result.safer.polyline.length / 2)];
const conv = await ingest(`/api/convergence?lat=${mid[0]}&lon=${mid[1]}&radius_m=400`)
```

Queries cameras at **one point** — the route's midpoint — within 400 m. A 1.3 km walk
gets whatever sits in a single circle in the middle; everything near the start and end is
invisible. This is the "always displays up to 3 cameras" symptom.

**Fix:** sample the whole path. `modules/harness/harness/routing.py:347` already does it
correctly and can be ported directly:

```python
sampled = coords[::EN_ROUTE_STRIDE]          # every 3rd coordinate, stride = 3
... cameras_for(radius_m=EN_ROUTE_RADIUS_M)  # 60 m, not 400 m
```

Note the radius change matters as much as the sampling: 400 m from one point pulls in
cameras that cannot see the walk, while 60 m along the path pulls in the ones that can.

### 2. IMPORTANT — `POST /api/route` does not validate its body
`modules/walk-app/server/index.ts:129-141` · owner: walk-app

```ts
const body = (await req.json()) as { origin?: [number, number]; dest?: [number, number] };
origin = body.origin ?? null;
```

A TypeScript cast is not a runtime check. Missing elements, non-numeric values, NaN or
out-of-range coordinates flow into `planRoute` → `NodeIndex.nearest()`, which destructures
`[lon, lat]` and does arithmetic on them. It fails safely but mislabels the failure as
"no walkable street near one of those points" (422).

The GET path at line 59-64 (`parseLatLon`) already checks `Number.isFinite` on both parts.
**Fix:** same check on POST — `Array.isArray`, length 2, `Number.isFinite` on both.
SPEC §7.4 and CLAUDE.md both require validating at boundaries; this is the one boundary in
the module that skips it.

### 3. MINOR — same midpoint collapse on the client
`modules/walk-app/src/App.tsx:98` · owner: walk-app

Identical `polyline[Math.floor(length/2)]` pattern in the "Around you" panel's fallback
when GPS is unavailable. Lower stakes — GPS position is preferred when present — but fix
it alongside #1 since it is the same one-line change.

### 4. MINOR — edge-id collision can silently drop a block after cache reload
`modules/walk-app/server/graph.ts:358-373` and `426-446` · owner: walk-app

`segment_id = sw:${way.id}:${a}` is keyed only on way id + start node. If one OSM way
revisits the same junction node (a loop, roundabout, dead-end loop), two distinct blocks
collide and `edges.set()` overwrites the first.

Masked on a fresh build because `adj` holds both edge objects directly — but
`saveGraph`/`loadGraph` rebuild `adj` from `[...edges.values()]`, so **after a restart
against a stale cache the overwritten block vanishes from the routable graph, silently.**

Not observed in the current downtown dataset; it needs specific way topology.
**Cheapest mitigation before the demo: delete `modules/walk-app/data/walk_graph.json` so
the graph rebuilds from source.** Proper fix (include the end node in the key) if there is
time.

### 5. IMPORTANT — `harness` fabricates collision counts and confidence
`modules/harness/harness/routing.py:140,272-274` · owner: harness

```python
collisions   = _jitter(w["id"], 0.05, 0.55) * (0.5 + traffic)
live_penalty = round(_jitter(w["id"] + 7,  -0.05, 0.03), 3)
confidence   = round(_jitter(w["id"] + 13,  0.40, 0.90), 2)
stale        = _jitter(w["id"] + 29, 0.0, 1.0) < 0.15
```

A PRNG seeded by OSM way id producing plausible-looking collision counts and confidence
scores. CLAUDE.md: *"Never fabricate data, numbers, or model output — not even
placeholders that look real."*

A judge asking where a collision number comes from has no answer. **Real SDOT collision
data is already joined** in `experiments/safe-walk/safewalk/graph.py`, so the placeholder
is not needed. Flagged independently in `modules/walk-app/SPEC.md`.

**Related decision, not a bug:** the repo has three routers (`experiments/safe-walk`,
`modules/harness`, `modules/walk-app`). Two are in play and they disagree. Which one ships
is a team call.

### 6. INFO — iOS blocks geolocation over plain HTTP
`modules/walk-app/vite.config.ts:14` · owner: walk-app

`host: true` exposes the dev server on the LAN so a phone can reach it — but that is plain
HTTP on a non-localhost address, and **iOS Safari refuses `navigator.geolocation` outside a
secure context.** localhost is exempt; `http://192.168.x.x:5173` and
`http://100.106.143.38:5173` are not.

The documented `cloudflared tunnel` path is HTTPS and avoids this. Worth one line in the
quickstart so nobody loses an hour to it. Same applies to any Capacitor/iOS work.

### 7. INFO — obstruction detection is unvalidated
`modules/vlm` · owner: vlm

78 hand-labelled frames contain **zero** confirmed obstructions, so the miss rate for
`blocked_sidewalk` / `narrowed_sidewalk` has never been measured. Both models produced
zero false alarms across 23 frames, which is real. Documented in `modules/vlm/RESULTS.md`
§4b, including two findings that were published and then retracted after the source
images were checked.

Not a code defect — a claim that should not be overstated on stage.

---

## Fixed

_(nothing yet — move entries here with the commit that closed them)_
