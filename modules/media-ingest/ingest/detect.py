"""Step 3 — street-level object-identification traversal over the graph.

BFS over every in-scope node; each camera's freshest frame (HLS segment
preferred — more up to date than snapshots) goes to the lightweight VLM
endpoint; the node is updated with what is CURRENTLY detected in its FOV.
Once a full pass completes, the sweep rests SWEEP_REST_S (10 s) and reruns.

Cameras are processed in parallel batches, but every upstream fetch still
respects the global rate gates in feeds.py (>=60 s/camera snapshots,
<=4 concurrent upstream) — the VLM endpoint has its own concurrency cap.

Object geolocation (3.2): each detection's normalized (cx, cy) is projected
into the world using the node's resolved bearing and an assumed FOV —
bearing_offset = (cx - 0.5) * FOV, range grows exponentially as cy rises
toward the horizon. This is a rough monocular estimate and is labeled as
such (`method: "fov-projection"`); when a camera's direction is unresolved,
detections carry NO position estimate rather than a fabricated one.

Priority (hot lane): POST :8030/priority marks cameras that get processed
first in every pass — synthesis/frontend use it for en-route cameras.

Single-camera entry point (3.1): analyze_camera(g, camera_id).
"""
from __future__ import annotations

import asyncio
import json
import threading
import time
from datetime import datetime, timezone

from . import config, feeds, vlm_client
from .graph import CameraGraph, dest_point

_STATE_LOCK = threading.Lock()
_LIVE: dict[str, dict] = {}          # camera_id -> last analysis result
_FOCUS: list[str] = []               # hot-lane camera ids, priority order

_status = {
    "running": False, "pass_no": 0, "last_pass_s": None,
    "analyzed": 0, "with_detections": 0, "started_at": None,
    "backend": None, "scope": config.SWEEP_SCOPE,
}
_stop_event = threading.Event()


def _iso_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ----------------------------------------------------------- geolocation

def project_detection(node: dict, det: dict) -> dict | None:
    """Rough world position for a detection, from the node's bearing.
    Returns {lat, lon, range_m, bearing_deg, method} or None if the
    camera's direction is unresolved (we don't guess)."""
    bearing = node.get("bearing") or {}
    if not bearing.get("resolved") and bearing.get("bearing_conf", 0) < 0.5:
        return None
    cam_bearing = bearing.get("bearing_deg")
    if cam_bearing is None:
        return None
    offset = (det["cx"] - 0.5) * config.ASSUMED_FOV_DEG
    obj_bearing = (cam_bearing + offset) % 360.0
    # cy=1.0 (bottom of frame) ~ nearest visible ground; cy<=0.5 ~ far field
    depth = max(0.0, min(1.0, (1.0 - det["cy"]) / 0.5))
    range_m = config.NEAR_RANGE_M * (config.FAR_RANGE_M / config.NEAR_RANGE_M) ** depth
    lat, lon = dest_point(node["lat"], node["lon"], obj_bearing, range_m)
    return {"lat": round(lat, 6), "lon": round(lon, 6),
            "range_m": round(range_m, 1), "bearing_deg": round(obj_bearing, 1),
            "method": "fov-projection"}


# ------------------------------------------------------- single camera (3.1)

def analyze_camera(g: CameraGraph, camera_id: str) -> dict | None:
    """Fetch the freshest frame for one camera, run the lightweight VLM
    read, update the node's live state. Returns the analysis or None."""
    node = g.nodes.get(camera_id)
    if node is None:
        return None
    blob, rec = feeds.latest_frame(node, prefer="hls")
    if blob is None:
        result = {"camera_id": camera_id, "analyzed_at": _iso_now(),
                  "ok": False, "why": "no frame (rate-gated, dead, or offline)"}
        with _STATE_LOCK:
            _LIVE[camera_id] = result
        return result
    out = vlm_client.detect_objects(blob)
    if out is None:
        result = {"camera_id": camera_id, "analyzed_at": _iso_now(),
                  "ok": False, "why": "no VLM backend configured/reachable",
                  "frame": rec}
        with _STATE_LOCK:
            _LIVE[camera_id] = result
        return result
    detections = []
    for d in out["detections"]:
        est = project_detection(node, d)
        detections.append({**d, "est": est})
    result = {
        "camera_id": camera_id,
        "analyzed_at": _iso_now(),
        "ok": True,
        "frame": rec,                       # FrameRecord §6.1 of what was read
        "model": vlm_client.backend(),
        "detections": detections,
        "caption": out["caption"],
    }
    with _STATE_LOCK:
        _LIVE[camera_id] = result
        node["live"] = {"analyzed_at": result["analyzed_at"],
                        "n_detections": len(detections)}
    return result


# ------------------------------------------------------------ sweep control

def set_focus(camera_ids: list[str]) -> list[str]:
    global _FOCUS
    with _STATE_LOCK:
        _FOCUS = list(dict.fromkeys(camera_ids))
    return _FOCUS


def live_state(camera_id: str | None = None) -> dict:
    with _STATE_LOCK:
        if camera_id:
            return _LIVE.get(camera_id) or {}
        return dict(_LIVE)


def sweep_status() -> dict:
    return {**_status, "focus": list(_FOCUS),
            "vlm_available": vlm_client.available()}


def _scope_ids(g: CameraGraph) -> list[str]:
    order = g.bfs()
    if config.SWEEP_SCOPE == "streets":
        order = [c for c in order if g.nodes[c].get("street_name")]
    with _STATE_LOCK:
        focus = [c for c in _FOCUS if c in g.nodes]
    rest = [c for c in order if c not in set(focus)]
    return focus + rest


async def _sweep_once(g: CameraGraph) -> dict:
    ids = _scope_ids(g)
    vlm_sem = asyncio.Semaphore(config.VLM_CONCURRENCY)
    loop = asyncio.get_running_loop()
    analyzed = with_det = 0

    async def one(cid: str) -> None:
        nonlocal analyzed, with_det
        if _stop_event.is_set():
            return
        async with vlm_sem:
            res = await loop.run_in_executor(None, analyze_camera, g, cid)
        if res and res.get("ok"):
            analyzed += 1
            if res["detections"]:
                with_det += 1

    # feeds.py's semaphore caps upstream at 4 regardless of task fan-out
    await asyncio.gather(*(one(c) for c in ids))
    _persist()
    return {"analyzed": analyzed, "with_detections": with_det, "cameras": len(ids)}


def _persist() -> None:
    with _STATE_LOCK:
        snap = dict(_LIVE)
    config.LIVE_STATE_JSON.write_text(json.dumps(snap, indent=1), encoding="utf-8")


async def run_sweep_loop(g: CameraGraph, log=print) -> None:
    """BFS traversal forever: full pass -> 10 s rest -> rerun."""
    _stop_event.clear()
    _status.update(running=True, started_at=_iso_now(),
                   backend=vlm_client.backend())
    try:
        while not _stop_event.is_set():
            t0 = time.monotonic()
            summary = await _sweep_once(g)
            _status.update(pass_no=_status["pass_no"] + 1,
                           last_pass_s=round(time.monotonic() - t0, 1),
                           analyzed=summary["analyzed"],
                           with_detections=summary["with_detections"])
            log(f"[sweep] pass {_status['pass_no']}: {summary} "
                f"in {_status['last_pass_s']}s")
            for _ in range(int(config.SWEEP_REST_S * 10)):
                if _stop_event.is_set():
                    break
                await asyncio.sleep(0.1)
    finally:
        _status["running"] = False


def stop_sweep() -> None:
    _stop_event.set()


def load_persisted() -> None:
    if config.LIVE_STATE_JSON.exists():
        try:
            saved = json.loads(config.LIVE_STATE_JSON.read_text(encoding="utf-8"))
            with _STATE_LOCK:
                _LIVE.update(saved)
        except json.JSONDecodeError:
            pass
