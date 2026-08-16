"""Binary activity flag per camera node — the "attention prior".

Pure pixel mechanics, no model calls: mean absolute difference between the
two most recent frames of a camera, on grayscale copies downscaled to
160x120. `active` means exactly "pixel change above threshold between two
timestamped frames" — never "person detected", "busy", or any safety word.

Hooks into feeds._emit (zero extra upstream requests — rides entirely on
frames we already pull). Guards, all required by the spec injection:

  * stale/placeholder frames        -> active: null, basis "stale"
  * luminance guard                 -> per-pixel median delta subtracted, so
                                       a global exposure shift isn't motion
  * PTZ guard                       -> >60% of pixels changed = the camera
                                       moved; active: null, basis
                                       "camera-moved", and the node's bearing
                                       confidence is downgraded
  * hysteresis                      -> flips active at THRESHOLD_HI, back at
                                       THRESHOLD_LO, no boundary flapping
  * source mismatch (hls/snapshot)  -> different crop/encode, no fair diff;
                                       basis "no-pair", pair reseeded
  * ACTIVITY_MAX_AGE_S              -> effective_activity() reports null for
                                       flags older than this ("right now"
                                       must mean right now)

`score` is stored for threshold tuning and demo-day debugging only — the
exported signal is the binary + timestamps.

Tuning harness: python -m ingest.activity  (runs over surukamera's cache)
"""
from __future__ import annotations

import threading
import time
from datetime import datetime, timezone

import cv2
import numpy as np

from . import config

_LOCK = threading.Lock()
_PREV: dict[str, dict] = {}   # camera_id -> {"gray": ndarray, "ts": float, "source": str}
_STATE: dict[str, bool] = {}  # camera_id -> last binary state (hysteresis)

# graph-persistence hook: service/detect register a throttled saver here
_SAVER = None
_updates_since_save = 0
_last_save = 0.0


def register_saver(fn) -> None:
    global _SAVER
    _SAVER = fn


def _maybe_save() -> None:
    global _updates_since_save, _last_save
    _updates_since_save += 1
    now = time.monotonic()
    if _SAVER and _updates_since_save >= 25 and now - _last_save > 30.0:
        _updates_since_save = 0
        _last_save = now
        try:
            _SAVER()
        except Exception:
            pass


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _downscale_gray(blob: bytes) -> np.ndarray | None:
    img = cv2.imdecode(np.frombuffer(blob, np.uint8), cv2.IMREAD_GRAYSCALE)
    if img is None:
        return None
    return cv2.resize(
        img, (config.ACTIVITY_DOWNSCALE_W, config.ACTIVITY_DOWNSCALE_H),
        interpolation=cv2.INTER_AREA,
    ).astype(np.float32)


def frame_delta(prev_gray: np.ndarray, cur_gray: np.ndarray) -> tuple[float, float]:
    """(mad, changed_frac): luminance-guarded mean absolute difference and
    the fraction of pixels changed beyond the PTZ pixel threshold."""
    delta = np.abs(cur_gray - prev_gray)
    changed_frac = float((delta > config.ACTIVITY_PTZ_PIXEL_DELTA).mean())
    # luminance guard: a global exposure/iris shift moves the whole
    # distribution; subtracting the median delta leaves only local change
    mad = float(np.maximum(delta - np.median(delta), 0.0).mean())
    return mad, changed_frac


def update(node: dict, blob: bytes | None, ts: float, source: str,
           stale: bool) -> dict:
    """Called from feeds._emit whenever a frame lands. Mutates the node's
    `activity` block (node dicts are references into the live CameraGraph)
    and returns it."""
    cid = node["camera_id"]
    now = time.time()

    def result(active, score, basis, prev_ts=None):
        block = {
            "active": active,
            "score": None if score is None else round(score, 2),
            "basis": basis,
            "frame_ts": _iso(ts),
            "prev_frame_ts": None if prev_ts is None else _iso(prev_ts),
            "computed_at": _iso(now),
        }
        node["activity"] = block
        if active:
            node["last_activity_at"] = _iso(ts)
        _maybe_save()
        return block

    if stale or blob is None:
        with _LOCK:
            _PREV.pop(cid, None)   # a dead camera's old frame is not a fair pair
            _STATE.pop(cid, None)
        return result(None, None, "stale")

    gray = _downscale_gray(blob)
    if gray is None:
        return result(None, None, "no-pair")

    with _LOCK:
        prev = _PREV.get(cid)
        _PREV[cid] = {"gray": gray, "ts": ts, "source": source}

    if prev is None or ts <= prev["ts"]:
        return result(None, None, "no-pair")
    if prev["source"] != source:
        # hls re-encode vs snapshot differ in crop/quality — not a fair diff
        return result(None, None, "no-pair", prev["ts"])

    mad, changed_frac = frame_delta(prev["gray"], gray)

    if changed_frac > config.ACTIVITY_PTZ_FRAC:
        with _LOCK:
            _STATE.pop(cid, None)
        # bonus per spec: a moved camera's precomputed bearing is suspect
        bearing = node.get("bearing")
        if bearing and bearing.get("basis") != "manual-confirmed" \
                and not bearing.get("ptz_downgraded"):
            bearing["bearing_conf"] = round((bearing.get("bearing_conf") or 0.35) * 0.7, 3)
            bearing["ptz_downgraded"] = True
            bearing["basis"] = (bearing.get("basis") or "") + "+camera-moved"
        return result(None, mad, "camera-moved", prev["ts"])

    with _LOCK:
        was_active = _STATE.get(cid, False)
        active = (mad >= config.ACTIVITY_THRESHOLD_HI if not was_active
                  else mad > config.ACTIVITY_THRESHOLD_LO)
        _STATE[cid] = active
    return result(active, mad, "pixel-delta", prev["ts"])


def effective_activity(node: dict) -> dict | None:
    """The activity block with ACTIVITY_MAX_AGE_S applied: a flag computed
    from old frames is not 'right now', so it reads back as null."""
    block = node.get("activity")
    if not block:
        return None
    try:
        computed = datetime.strptime(
            block["computed_at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        age = (datetime.now(timezone.utc) - computed).total_seconds()
    except (KeyError, ValueError):
        return None
    if age > config.ACTIVITY_MAX_AGE_S and block["active"] is not None:
        return {**block, "active": None, "basis": "stale"}
    return block


# ------------------------------------------------------------------ tuning

def _tune(cache_root) -> None:
    """Run the signal over surukamera's cached snapshot pairs (read-only) to
    sanity-check the thresholds. Prints the MAD distribution — no
    ground-truth labels here, so this is a distribution check, not accuracy."""
    from pathlib import Path
    root = Path(cache_root)
    mads, moved = [], 0
    for cam_dir in sorted(root.iterdir()):
        frames = sorted(cam_dir.glob("*.jpg"))
        if len(frames) < 2:
            continue
        g0 = _downscale_gray(frames[-2].read_bytes())
        g1 = _downscale_gray(frames[-1].read_bytes())
        if g0 is None or g1 is None:
            continue
        mad, frac = frame_delta(g0, g1)
        if frac > config.ACTIVITY_PTZ_FRAC:
            moved += 1
            continue
        mads.append((mad, cam_dir.name))
    mads.sort()
    arr = np.array([m for m, _ in mads])
    print(f"pairs={len(arr)} ptz-guard fired={moved}")
    for q in (10, 25, 50, 75, 90, 95):
        print(f"  p{q:<3} MAD = {np.percentile(arr, q):.2f}")
    hi, lo = config.ACTIVITY_THRESHOLD_HI, config.ACTIVITY_THRESHOLD_LO
    print(f"  thresholds hi={hi} lo={lo} -> "
          f"{(arr >= hi).mean() * 100:.0f}% would flag active")
    print("  top 5:", [(round(m, 1), c) for m, c in mads[-5:]])
    print("  bottom 5:", [(round(m, 1), c) for m, c in mads[:5]])


if __name__ == "__main__":
    _tune(config.REPO_ROOT / "experiments" / "surukamera" / "cache" / "snapshots")
