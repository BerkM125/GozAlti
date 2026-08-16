# GozAlti

Seattle under the eye: live pedestrian-safety routing built on the city's 646
public traffic cameras, a VLM on an NVIDIA DGX Spark (GB10), and evidence-first
safety assessments. NVIDIA Spark Hack: Seattle, Aug 14–16 2026 — **See** track.

**Start here → [`SPEC.md`](SPEC.md)** — the god spec: mission, demo definition
of done, module ownership, data contracts, and engineering rules. Claude
sessions are bound to it via [`CLAUDE.md`](CLAUDE.md).

| Where | What |
|---|---|
| `SPEC.md` | God spec — read before any work |
| `modules/<name>/SPEC.md` | Your module's scope, contracts, definition of done |
| `experiments/safe-walk/` | Base layer: routing, scraping, VLM captioning, risk model (read-only) |
| `experiments/surukamera/` | Base layer: camera↔street index, HLS discovery, bearing stack (read-only) |
| `demo/` | Run-of-show, filming plan, fallback recordings, submission |

Team: Ioli (map-frontend, harness) · Adi (vlm) · Berkan (media-ingest) ·
Dhruv (osint, Spark endpoints) · All (synthesis, demo).
