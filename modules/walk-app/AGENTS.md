# walk-app architecture

Mobile walking app for GozAlti: React + Bun + Vite, with pedestrian routing computed in-process.
~3,860 lines. `SPEC.md` covers what it does and how to run it; this file covers how it is built and why.

## Lane

Everything lives under `modules/walk-app/`.
Do not edit `modules/map-frontend` (Ioli's module) or anything under `experiments/` (read-only).

## Data flow

```
experiments/surukamera/data/osm_ways.json   (17 MB, committed)
        │  parsed once at boot, cached to data/walk_graph.json
        ▼
  server/graph.ts ──► WalkGraph {nodes, adj, edges}   5,435 junctions / 6,579 blocks
        │
        ▼
  server/routing.ts ──► Dijkstra | A*  ──► Route + SegmentAssessment[]
        │
        ▼
  server/index.ts  Bun.serve :8020 ──────────────┬──► GET/POST /api/route      (computed here)
        │                                        ├──► GET /api/geocode         (server/search.ts, offline)
        │                                        └──► /api/cameras|detections|frame|hls
        │                                                    │  proxied, never SDOT directly
        │                                                    ▼
        │                                        media-ingest :8030
        ▼
  dist/  (production: same origin, one port, so cloudflared tunnels a single URL)
        ▲
        │  dev: Vite :5173 proxies /api ──► :8020
        ▼
  src/App.tsx ──► SearchBar (trip planner) + MapView (MapLibre) + Panels (sheets) + CameraTile (HLS/snapshot)
```

One port in production is deliberate: `cloudflared tunnel --url http://localhost:8020` is the whole demo deployment.

## Files

**Server**

| File | Lines | Role |
|---|---|---|
| `server/graph.ts` | 488 | OSM parse, walkability filter, block splitting, risk scoring, disk cache |
| `server/routing.ts` | 404 | Min-heap, spatial node index, Dijkstra/A\*, contract shaping |
| `server/index.ts` | ~400 | `Bun.serve` routes, media-ingest proxy, SSE, static `dist/` |
| `server/search.ts` | 208 | Offline geocoder: street + intersection index over the graph, abbreviation-aware matching |
| `server/cameras.ts` | 170 | §6.7 shaping, and which cameras watch a route |
| `server/smoke.ts` | 122 | 13 assertions incl. A\* optimality vs Dijkstra, and place search |

**Client**

| File | Lines | Role |
|---|---|---|
| `src/App.tsx` | ~560 | All state (incl. the trip and pick-on-map), data fetching, ahead/behind placement |
| `src/components/MapView.tsx` | ~400 | MapLibre lifecycle, route layers, HTML markers, 2D/3D |
| `src/components/SearchBar.tsx` | 340 | Destination bar, two-stop planner, suggestion dropdown |
| `src/mapStyle.ts` | 285 | The basemap style: palette, road classes, building extrusion |
| `src/components/Panels.tsx` | 305 | Sheet shell, segment evidence, vertical camera feed, around-you |
| `src/components/CameraTile.tsx` | ~230 | HLS with snapshot fallback, age badge, detection overlay |
| `src/types.ts` | ~220 | SPEC §6 contract shapes, Place/TripStop, detection family mapping |
| `src/theme.css` | ~160 | Inter @font-face, glass tokens and the colour budget, documented inline |
| `src/styles.css` | ~1320 | Layout, search/planner, feeds tab, sheets, markers, MapLibre overrides |
| `src/api.ts` / `src/config.ts` | ~140 | Fetch wrappers incl. `searchPlaces`; centre, pitch, thresholds |

## Graph construction

Nodes are junctions, edges are the stretch of one OSM way between two junctions, which is a city block.
That is the unit the evidence sheet talks about ("Pike St, 1st Ave → 2nd Ave").

- A node is a junction if two or more kept ways use it, or it is a way endpoint.
- Shared OSM node IDs make junctions exact. safe-walk rounds coordinates into 12 m buckets and infers them.
- `segment_id` = `sw:<way_id>:<start_node_id>`, stable across rebuilds. safe-walk's positional integer renumbers on every re-fetch.
- Sub-metre stubs are dropped as digitisation noise.
- Cross-street names come from the other ways meeting at each endpoint.
- Freeways are never walkable. Trunk roads are admitted only when OSM positively records a sidewalk.

Build is ~90 ms with zero network calls, then cached to `data/walk_graph.json` (gitignored, `CACHE_VERSION` invalidates it).

## Cost model

```
cost(edge) = length_m × (1 + riskWeight × risk)
```

Metres of *effective* walking. `riskWeight` is 0 for the direct route, 3.0 for the recommended one.

`risk` in [0,1] is four weighted components, each read from a real OSM tag on that block:

| Component | Weight | Tags |
|---|---|---|
| Sidewalk | 0.34 | `sidewalk`, `sidewalk:both`, `sidewalk:left`, `sidewalk:right` |
| Traffic | 0.28 | `highway` class, `maxspeed` |
| Lighting | 0.20 | `lit` |
| Crossing | 0.18 | `lanes` |

Missing tags get a stated neutral default and are flagged `inferred`.
About 72% of downtown blocks have at least one inferred component, nearly all of them `lit`.

**Slope is deliberately absent.** Seattle hills matter for walking, but this dump has no elevation, and a fabricated grade is worse than none.

## Search

Dijkstra and A\* share one relaxation loop; the only difference is the heuristic, and Dijkstra passes `() => 0`.

A\* uses straight-line distance, admissible because `risk >= 0` means cost is never below length, so A\* returns Dijkstra's exact optimum.
Measured Pike Place → Pioneer Square: identical cost, **18 junctions expanded vs 429**.

`NodeIndex` buckets junctions into a ~445 m degree grid and widens by rings, plus one ring past the first hit since a nearer node can sit across a cell boundary.
safe-walk scans every node linearly per endpoint.

## Basemap and 3D

The basemap is a **vector** style written by hand in `src/mapStyle.ts`, not a vendor style and not raster.
Raster was the original choice and had to go for two reasons: a raster tile is a picture, so its colours cannot be matched to the interface, and it carries no building geometry, so there is nothing to extrude.

Tiles come from **OpenFreeMap** (OpenMapTiles schema, OSM data). No API key, no account, no build step, and it also serves the glyphs the label layers need.

The 3D toggle does three things at once:

1. Pitches the camera to 58°.
2. Swaps `building-flat` (a `fill` layer) for `building-3d` (a `fill-extrusion` layer). They are the same geometry, so drawing both z-fights.
3. Raises zoom to at least 15, because extrusions only exist from z14 and a tilted view above that zoom shows a flat city.

Heights are real: `render_height` per feature, with `render_min_height` as the base so a building on a podium starts at the right level. `fill-extrusion-vertical-gradient` shades walls darker than roofs, which is what makes massing read as solid. Untagged buildings fall back to 9 m, roughly three storeys.

For the record, "wireframe" is a different thing: edges only, no filled faces. This is extrusion.

With a route on screen, toggling 3D re-runs `fitBounds` with the new pitch instead of easing, otherwise tilting pushes the destination off the edge.

## Design system

Light only. Dark mode was removed.

Glass: floating translucent panels over the live map rather than opaque bars, so the interface never fully covers the thing it describes.
Depth is `backdrop-filter: blur(24px) saturate(180%)` plus an inner highlight and a hairline, never a heavy shadow.
Every floating panel inherits one `.glass` rule. Sheets use a stronger blur because they carry body text over a busy frame.
There is an `@supports not (backdrop-filter)` fallback to near-opaque white, so text never lands on bare map.

Colours are Apple's system palette verbatim, not approximations, so the blue reads as the blue an iOS user already knows.
Text colours are the darker variants (`#248A3D`, `#995700`, `#C00D02`) because the system swatches themselves are too light to pass contrast on white.

## Invariants

These are product rules, not preferences. Breaking one is a bug.

1. **No aggregate safety score reaches the UI.** `risk` is surfaced only under its honest name - the "routing weight" map layer, legend and scale bars, with the four inputs on tap - never as a safety/danger number or verdict, and never as a printed aggregate.
2. **Every camera image carries an age badge**, sourced from the §6.1 `FrameRecord`, never from the VLM. Tying it to an `Observation` means no badge until a model has read the camera, which is how you end up with a bare `SNAP` that asserts nothing. `LIVE` only while a stream is actually playing; otherwise `STREAM 6s` / `SNAP 45s` / `CACHED 12m` naming the frame's source, `NO FRESH FRAME` when `stale`, `AGE UNKNOWN` when no record arrived. Snapshot refresh is 60 s, matching media-ingest's per-camera floor.
3. **Detections with no `est` are never placed on the map.** They go to a "seen, but not placed" group stating the camera's bearing is unresolved. No position is ever estimated.
4. **View cones only for resolved bearings**, at opacity scaled by `bearing_conf`.
5. **Colour carries fixed meanings.** Blue `#007AFF` is the recommended route and the chrome. The muted weight ramp (green `#4D9E58` to brick `#B23A2C`, `palette.ts WEIGHT_STOPS` / `--ramp`) is routing weight only, on the map and in scale bars. Green `#34C759` marks only a live/healthy feed. Orange `#FF9500` is flagged/unmapped fills. Red `#FF3B30` is reserved for live alerts and refusals. Camera markers stay neutral for the same reason.
6. **Ahead/behind is measured along the route, not by compass.** With no route active the panel says the split is not knowable rather than guessing.
7. **No upstream call bypasses media-ingest.** All SDOT rate-limit discipline stays in one place. This is why `/api/cameras` is built from media-ingest's `/api/nearby` rather than forwarding `/api/convergence`: the contract endpoint hands out the raw Wowza URL, and a browser running this bundle following it *is* this module calling SDOT. See SPEC.md for the §6.7 deviation this implies.
9. **At most one camera streams live.** media-ingest's HLS proxy has no rate gate, so each playing tile is one continuous viewer on SDOT's stream host. Live HLS is confined to the full-screen viewer; list tiles are snapshots. Proxying is not throttling, and the SPEC says so out loud.
8. **Unavailable upstreams render as `{ok:false, why}`**, never a 500 and never a fabricated value.

## Gotchas

Each of these cost real debugging time.

- **`RasterTileSource.setTiles()` updates the URL but leaves the tile cache marked loaded**, so the map goes blank. This is why the old light/dark raster swap was done with layer visibility, and part of why the basemap is vector now.
- **`fill` and `fill-extrusion` on the same source-layer z-fight.** Only one of `building-flat` / `building-3d` is ever visible.
- **OpenFreeMap serves Noto Sans in Regular, Italic and Bold only.** Any other `text-font` silently drops the layer.
- **Bare-number road names are ferry berth and parking-lot markers** that render as floating digits over the water. Road labels filter out `service` / `track` classes and anything that parses as a number.
- **Never set `position` on a marker class.** MapLibre anchors each marker by making its root `position: absolute` (`.maplibregl-marker`), and styles.css loads after maplibre-gl.css at equal specificity - `.mk-cam { position: relative }` silently won, dropped every camera marker into normal flow, and displaced each one by 28 px times its list index, drifting as the list reordered. Latent since the module's first commit; invisible until cameras actually rendered. The absolutely-positioned root is itself a positioned ancestor, so `.mk-cone` never needed the `relative`.
- **Camera markers appear only with a route** (plus the one camera open in the viewer). The corridor is what gives map markers their meaning; without a route the panel lists what is near you and the map stays clean.
- **Map markers are HTML `Marker`s, not symbol layers.** Vector tiles now give us glyphs, so symbol layers are possible, but DOM markers keep full CSS control of the glass and cone treatments. Fine at the tens of markers this app shows.
- **Two `useState` setters read a stale closure** when taps land in the same tick, which routed a point to itself. Origin and destination are one atomic state object.
- **A backgrounded tab throttles `requestAnimationFrame` to zero**, so MapLibre never paints and the map looks broken while the DOM chrome renders fine. Check `document.hidden` before debugging the map. Verify through a CDP-driven browser.
- **`bunx tsc` may resolve a global TypeScript**, not the pinned local one. Use `bun run build`, which runs `tsc --noEmit` first.
- The style's `load` event fires after the first render, so sources and layers added in `on("load")` never appear if rendering is blocked.
- **`video.canPlayType("application/vnd.apple.mpegurl")` returns `"maybe"` in Chromium**, which is truthy, so "ask the element first" routes every desktop browser down the native-HLS path where it fails with `DEMUXER_ERROR_COULD_NOT_PARSE`. Test `Hls.isSupported()` first and treat native as the Safari fallback.
- **A `<video>` left mounted after the stream falls back is a black box** over a perfectly good snapshot. The element has to unmount when the feed degrades, not just stop.
- **media-ingest's first frame for a camera takes 8-10 s** while it pulls upstream; later ones are cached and instant. Proxy timeouts below ~20 s turn that into a silent 503 and an empty tile. media-ingest allows itself 20 s, so anything shorter here gives up on a frame it was going to get.
- **Setting only `overflow-y` makes `overflow-x` compute to `auto`**, which gave the sheet a second stray horizontal scrollbar under the old camera carousel. Set both. Clipping happens at the *padding* box, so a `-16px` bleed survives `overflow-x: hidden` only if the padding lives on the scroll container itself.
- **The horizontal camera carousel is gone** (replaced by the vertical `.cam-feed` behind the feeds tab), so its snap gotcha is historical: `scroll-snap-align: start` snaps to the scrollport edge and needs `scroll-padding-left` to keep an inset. Remember it if a carousel ever comes back.
- **A route is a line, so a radius query is the wrong shape for it.** Asking for cameras near the route's midpoint returns the same few no matter how long the walk is. Measure every camera against the polyline instead: `camerasAlongRoute` in `server/cameras.ts`, ordered by `along_m` so the list reads in passing order. Sampling the route with repeated radius queries is the tempting shortcut and it both misses cameras between samples and double-counts the ones near them.
- **Uncapped camera lists are fine because `<img loading="lazy">` is doing the work.** A 28-camera route fetches 4 frames, not 28. Anything that forces layout or eagerly loads those images turns one route into 28 rate-gated upstream fetches.
- **`/api/detections/{cid}` answers `{}`**, not an error, for a camera nothing has analysed. `ok === false` does not catch it, so it gets stored as a real observation and the tile says "no description returned" when the truth is "never read". Guard with `isObservation`.

## Verify

```bash
bun run smoke   # 8 assertions, incl. A* == Dijkstra at riskWeight 0 and 3
bun run build   # typecheck + bundle
bun run dev     # API :8020, Vite :5173
```

For UI changes, check 2D and 3D at 414×896, and confirm the evidence sheet still names its OSM tags.
In 3D, confirm buildings actually extrude: `__map.queryRenderedFeatures({layers:['building-3d']})` should return hundreds of features with non-null `render_height`.
