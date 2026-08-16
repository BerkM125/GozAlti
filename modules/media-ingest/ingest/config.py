"""Central config for media-ingest. Every knob that changes between a laptop
and the Spark lives here, mirroring safe-walk's config.py."""
from __future__ import annotations

import os
from pathlib import Path

MODULE_ROOT = Path(__file__).resolve().parent.parent          # modules/media-ingest
REPO_ROOT = MODULE_ROOT.parent.parent                          # GozAlti/

DATA = MODULE_ROOT / "data"                                    # gitignored
FRAMES = DATA / "frames"
SEGMENTS = DATA / "segments"
SATELLITE = DATA / "satellite"
TILES = DATA / "tiles"
SAT_TILES = DATA / "sat_tiles"
for _d in (DATA, FRAMES, SEGMENTS, SATELLITE, TILES, SAT_TILES):
    _d.mkdir(parents=True, exist_ok=True)

# --- artifacts ------------------------------------------------------------
GRAPH_JSON = DATA / "camera_graph.json"          # the positional artifact (step 1)
MANUAL_BEARINGS = DATA / "manual_bearings.json"  # human-confirmed FOV directions
SATVLM_CACHE = DATA / "satellite_vlm.json"       # VLM frame<->satellite verdicts
PLACEHOLDER_HASHES = DATA / "placeholder_hashes.json"
FRAME_RECORDS_JSONL = DATA / "frame_records.jsonl"
LIVE_STATE_JSON = DATA / "live_state.json"       # last detections per node

# --- bootstrap sources (shipped, offline) ---------------------------------
SURU_DATA = REPO_ROOT / "experiments" / "surukamera" / "data"
SURU_CAMERAS = SURU_DATA / "cameras.json"
SURU_STREETS = SURU_DATA / "streets.json"

# --- rate discipline (SPEC §7.5 — non-negotiable) -------------------------
SNAPSHOT_MIN_INTERVAL_S = float(os.getenv("SNAPSHOT_MIN_INTERVAL_S", "60"))
HLS_MIN_INTERVAL_S = float(os.getenv("HLS_MIN_INTERVAL_S", "10"))
FETCH_CONCURRENCY = int(os.getenv("FETCH_CONCURRENCY", "4"))   # upstream cap
FETCH_TIMEOUT = float(os.getenv("FETCH_TIMEOUT", "15"))
USER_AGENT = os.getenv(
    "INGEST_UA",
    "gozalti-media-ingest/0.1 (NVIDIA Spark Hack Seattle 2026 research; "
    "contact: berkanm@uw.edu)",
)

# --- detection sweep ------------------------------------------------------
SWEEP_REST_S = float(os.getenv("SWEEP_REST_S", "10"))          # between full passes
SWEEP_SCOPE = os.getenv("SWEEP_SCOPE", "streets")              # "streets" | "all"
VLM_CONCURRENCY = int(os.getenv("VLM_CONCURRENCY", "2"))
# hot-lane cadence: active/focus cameras every pass, the rest every Nth pass
SLOW_LANE_EVERY_N = int(os.getenv("SLOW_LANE_EVERY_N", "3"))

# --- activity flag (attention prior — pure pixel mechanics, no model) -----
ACTIVITY_DOWNSCALE_W = int(os.getenv("ACTIVITY_DOWNSCALE_W", "160"))
ACTIVITY_DOWNSCALE_H = int(os.getenv("ACTIVITY_DOWNSCALE_H", "120"))
# MAD thresholds on 0-255 grayscale after median-delta subtraction.
# Defaults tuned against experiments/surukamera/cache/snapshots pairs —
# see ingest/activity.py __main__ for the tuning harness.
ACTIVITY_THRESHOLD_HI = float(os.getenv("ACTIVITY_THRESHOLD_HI", "4.0"))
ACTIVITY_THRESHOLD_LO = float(os.getenv("ACTIVITY_THRESHOLD_LO", "2.0"))
# fraction of pixels changed beyond which we assume the camera itself moved
ACTIVITY_PTZ_FRAC = float(os.getenv("ACTIVITY_PTZ_FRAC", "0.60"))
ACTIVITY_PTZ_PIXEL_DELTA = float(os.getenv("ACTIVITY_PTZ_PIXEL_DELTA", "25"))
ACTIVITY_MAX_AGE_S = float(os.getenv("ACTIVITY_MAX_AGE_S", "300"))

# --- temporal breadcrumbs / VLM forward path ------------------------------
# hot-lane push target (Adi/Dhruv's vlm module) and optional synthesis sink
VLM_READ_URL = os.getenv("VLM_READ_URL", "")     # e.g. http://localhost:8040/read
SYNTH_OBS_URL = os.getenv("SYNTH_OBS_URL", "")   # optional forward of Observations
OBSERVATIONS_DIR = DATA / "observations"
OBSERVATIONS_DIR.mkdir(parents=True, exist_ok=True)
PRIOR_OBSERVATIONS_N = int(os.getenv("PRIOR_OBSERVATIONS_N", "3"))

# --- VLM endpoint (OpenAI-compatible; the Spark NIM / vlm module on :8040) -
VLM_BASE_URL = os.getenv("VLM_BASE_URL", "")                   # e.g. http://localhost:8040/v1
VLM_MODEL = os.getenv("VLM_MODEL", "nvidia/cosmos-reason1-7b")
VLM_API_KEY = os.getenv("VLM_API_KEY", "not-needed")
VLM_TIMEOUT = float(os.getenv("VLM_TIMEOUT", "60"))
# Fallback: Anthropic vision (used by orientation cross-checks when no NIM yet)
ANTHROPIC_KEY = os.getenv("VISION_API_KEY") or os.getenv("ANTHROPIC_API_KEY") or ""

# --- geometry assumptions for object geolocation --------------------------
ASSUMED_FOV_DEG = float(os.getenv("ASSUMED_FOV_DEG", "60"))
NEAR_RANGE_M = float(os.getenv("NEAR_RANGE_M", "8"))
FAR_RANGE_M = float(os.getenv("FAR_RANGE_M", "120"))

# --- retention ------------------------------------------------------------
KEEP_RECENT_PER_CAM = int(os.getenv("KEEP_RECENT_PER_CAM", "12"))

# --- graph build ----------------------------------------------------------
PROXIMITY_EDGE_M = float(os.getenv("PROXIMITY_EDGE_M", "300"))
GRID_CELL_M = 150.0

# --- upstream endpoints ---------------------------------------------------
HLS_BASE = "https://61e0c5d388c2e.streamlock.net:443/live"
ESRI_TILE = ("https://server.arcgisonline.com/ArcGIS/rest/services/"
             "World_Imagery/MapServer/tile/{z}/{y}/{x}")
DARK_TILE = "https://basemaps.cartocdn.com/dark_all/{z}/{x}/{y}.png"
OVERPASS = "https://overpass-api.de/api/interpreter"
OVERPASS_MIRROR = "https://overpass.kumi.systems/api/interpreter"
