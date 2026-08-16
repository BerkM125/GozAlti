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
