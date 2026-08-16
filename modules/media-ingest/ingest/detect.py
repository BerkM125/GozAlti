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

from . import activity, config, feeds, vlm_client, vlm_forward
from .graph import CameraGraph, dest_point

_STATE_LOCK = threading.Lock()
_LIVE: dict[str, dict] = {}          # camera_id -> last analysis result
_FOCUS: list[str] = []               # hot-lane camera ids, priority order

_status = {
    "running": False, "pass_no": 0, "last_pass_s": None,
    "analyzed": 0, "with_detections": 0, "started_at": None,
    "backend": None, "scope": config.SWEEP_SCOPE,
    "activity_true": 0, "activity_false": 0,
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
        # co-presence: mechanical "last person in view" — evidence attached,
        # no inference beyond the detection itself
        if any(d["label"] == "person" for d in detections):
            node["copresence"] = {
                "last_person_at": result["analyzed_at"],
                "seen_by": camera_id,
                "source": "fastlane-detect",
                "frame": rec.get("path") if rec else None,
            }
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


def _lane_ids(g: CameraGraph, pass_no: int) -> list[str]:
    """Hot-lane cadence: focus + pixel-active + unknown-activity cameras run
    every pass; explicitly inactive ones every SLOW_LANE_EVERY_N passes.
    The cheap activity signal is what decides who gets fetch budget."""
    ids = _scope_ids(g)
    if pass_no % config.SLOW_LANE_EVERY_N == 0:
        return ids   # periodic full-city pass keeps inactive flags fresh
    focus = set(_FOCUS)
    kept = []
    for cid in ids:
        if cid in focus:
            kept.append(cid)
            continue
        act = activity.effective_activity(g.nodes[cid])
        if act is None or act.get("active") is not False:
            kept.append(cid)   # active, or no fair pair yet — keep sampling
    return kept


async def _sweep_once(g: CameraGraph, pass_no: int) -> dict:
    ids = _lane_ids(g, pass_no)
    vlm_sem = asyncio.Semaphore(config.VLM_CONCURRENCY)
    # bound in-flight work so a stop request drains in seconds, not minutes:
    # without this every camera's fetch is already queued in the executor
    # before the stop event can be observed
    flight_sem = asyncio.Semaphore(config.FETCH_CONCURRENCY * 2)
    loop = asyncio.get_running_loop()
    analyzed = with_det = 0
    vlm_on = vlm_client.available()

    async def one(cid: str) -> None:
        nonlocal analyzed, with_det
        async with flight_sem:
            await _one_inner(cid)

    async def _one_inner(cid: str) -> None:
        nonlocal analyzed, with_det
        if _stop_event.is_set():
            return
        if vlm_on:
            async with vlm_sem:
                res = await loop.run_in_executor(None, analyze_camera, g, cid)
            if res and res.get("ok"):
                analyzed += 1
                if res["detections"]:
                    with_det += 1
        else:
            # no VLM backend: still fetch the frame so the pixel activity
            # flag keeps updating — that alone earns the pass
            await loop.run_in_executor(None, feeds.latest_frame,
                                       g.nodes[cid], "hls")
        # hot lane: focus cameras also go to the vlm module with breadcrumbs
        if cid in _FOCUS and vlm_forward.enabled():
            await loop.run_in_executor(None, vlm_forward.read_camera, g.nodes[cid])

    # feeds.py's semaphore caps upstream at 4 regardless of task fan-out
    await asyncio.gather(*(one(c) for c in ids))
    _persist()
    g.save()   # activity flags + last_activity_at land in the on-disk artifact
    flags = [activity.effective_activity(n) for n in g.nodes.values()]
    return {"analyzed": analyzed, "with_detections": with_det, "cameras": len(ids),
            "activity_true": sum(1 for f in flags if f and f.get("active") is True),
            "activity_false": sum(1 for f in flags if f and f.get("active") is False)}


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
            summary = await _sweep_once(g, _status["pass_no"] + 1)
            _status.update(pass_no=_status["pass_no"] + 1,
                           last_pass_s=round(time.monotonic() - t0, 1),
                           analyzed=summary["analyzed"],
                           with_detections=summary["with_detections"],
                           activity_true=summary["activity_true"],
                           activity_false=summary["activity_false"])
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
