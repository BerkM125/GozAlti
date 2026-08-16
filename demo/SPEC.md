# demo/ — Demo & Video

**Owners:** minimum 3 people · **ESSENTIAL TO DEMO** · Submissions Sunday Aug 16, by 4:00 PM PDT

Read `../SPEC.md` §3 first — the four things the demo must show are the north
star for the whole repo.

## What lives here

- `run-of-show.md` — minute-by-minute demo script: who walks, who presents, who
  drives the laptop, which streets (pick ones with live-HLS cameras from
  harness's convergence output), the staged alert moment.
- `filming.md` — shot list: phone screen capture of the app while walking +
  a second phone filming the walker + (money shot) the SDOT camera's own view
  of the walker. Sync all clocks before filming.
- `fallback/` — recordings of every working flow, captured as soon as it works,
  re-captured when it improves. If venue Wi-Fi or an upstream feed dies on
  stage, we present from here without missing a beat.
- `submission.md` — track (See), VSS/Spark usage statement, judge-facing claims
  (each one checkable — link the evidence), team credits, data attribution
  (City of Seattle / SDOT, OpenStreetMap contributors, CARTO).

## Practices

- Rehearse the full demo at least twice before Sunday morning, once on venue
  Wi-Fi and once on a phone hotspot.
- Suspend background sweeps during the live demo so hot-lane reads get the whole
  GPU (safe-walk's `scripts/demo_mode.sh` is the pattern — port it).
- The walker's route must have ≥2 cameras with live HLS on it; verify the night
  before AND the morning of (PTZ cameras get re-aimed).
- Every claim in the pitch has evidence one click away. We never say "safety
  score"; we say what the cameras, the collision record, and the internet
  actually show.
