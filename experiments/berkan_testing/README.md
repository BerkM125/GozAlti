# berkan_testing — media-ingest dev console (NOT COMMITTED)

Throwaway UI for exercising `modules/media-ingest`. This whole directory is
gitignored; every capability it demonstrates lives in the module, not here.

## Run

```bash
cd modules/media-ingest
.venv/Scripts/activate          # or your env
uvicorn ingest.service:app --port 8030
# open http://localhost:8030/  — the service auto-mounts this directory
```

## What to test

- **Click anywhere on the map** → 100 m radius query (`/api/nearby`), the
  cameras that see that spot appear as chips; nearest feed opens (live HLS
  when the camera has a stream, snapshot otherwise).
- **Left rail** → street list (`/api/streets`); click a street to see its
  cameras in along-street order.
- **FOV cones** — teal = resolved bearing (opacity = confidence), amber =
  your pending rotation. Rotate with ±5/±15/±45/FLIP/N-E-S-W, watch the cone
  live, then CONFIRM (POST `/api/bearing/{cid}`, layer L0 manual, conf 0.95)
  or CLEAR → auto layers.
- **SAT / 3D / BLDG** — satellite basemap, tilt, OSM building extrusions for
  the current view (zoom in first).
- **PATH** — click A then B → evidence-weighted safer route. The mode
  **disables itself once the path is entered** (route stays drawn; press
  PATH again for a fresh one). Click any segment for the **deterministic
  factor popup**: every ± term of the server's RISK_FORMULA
  (base structural risk, night+unlit, camera coverage, open refuges)
  with the evidence behind it. On render, every en-route camera gets
  **one still-frame detlib pass** (Adi's stack, source of truth, ≤4
  concurrent) — people/vehicle boxes land on the map, tally in the cambar.
- **TAKE ME TO** — one click → route from YOUR current location to that
  spot. Enables location automatically (LOC flow, map-center fallback on
  plain HTTP) if it isn't already on; also self-disables after the click.
- **DET dots** — object detections projected to world coords from each
  camera's bearing; needs `VLM_BASE_URL` (Spark NIM) or `ANTHROPIC_API_KEY`
  set on the service. Without a backend, nodes carry no detections.
- **Sweep panel** — start/stop the BFS detection traversal, watch pass
  timing and counts.
