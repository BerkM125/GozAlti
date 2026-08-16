# modules/media-ingest — Media Data Scraping/Analysis (model's input)

**Owner:** Berkan · **Effort budget:** 3 h

Read `../../SPEC.md` first. Lane: this directory only.

## Scope

Get footage and structure it as the VLM's input feed, as a service on **:8030**:

- **Sources**: SDOT snapshots (all ~646 cameras) and HLS live streams (357
  cameras). Harvest `experiments/safe-walk/safewalk/scraper.py` (sweep daemon,
  dead-camera placeholder detection) and surukamera's stream knowledge
  (`experiments/surukamera/app/manifest.py`, `docs/ENDPOINTS.md` — Wowza URL
  template, stream keys). Optionally the presenter's phone camera as a source
  for the demo.
- **Cadence & priority**: full-city sweep at a slow cadence; en-route/selected
  cameras on a fast lane (safe-walk's `SWEEP_INTERVAL` is 900 s on a laptop,
  60 s on the Spark). Synthesis tells you which cameras are hot
  (`POST :8030/priority` with a camera-id list).
- **Rate-limit discipline is this module's core responsibility**: ≥60 s per
  camera between snapshot fetches, ≤4 concurrent upstream requests, descriptive
  User-Agent, exponential backoff on errors. For HLS, pull short TS segments —
  don't hold hundreds of streams open.
- **Storage & feed**: frames under `data/frames/<camera_id>/<ts>.jpg`
  (gitignored), sized/cropped to what the VLM wants; emit `FrameRecord` (§6.1)
  per capture and push hot-lane frames straight to the VLM (`POST :8040/read`),
  forwarding the returned `Observation` to synthesis.
- Serve `GET :8030/frame/<camera_id>/latest.jpg` so the frontend can show raw
  previews without touching SDOT directly.

**Out of scope:** interpreting imagery (vlm), safety decisions (synthesis),
non-media sources (osint).

## Contracts

Produces `FrameRecord` (§6.1). Set `stale: true` on placeholder detection so the
VLM can skip it.

## Definition of done (demo)

Hot-lane cameras along the demo route refresh every ≤60 s on the Spark; the VLM
receives well-formed `FrameRecord`s continuously; no upstream 429s/bans during a
2-hour run; disk use bounded (prune old frames).

## Practices

- One asyncio process, config knobs via env (mirror safe-walk's `config.py`).
- Log every fetch with camera id + outcome; a per-camera "last success" endpoint
  makes demo-day debugging trivial.
- Test the pipeline against `experiments/surukamera/cache/snapshots/` before
  hitting the live network.
