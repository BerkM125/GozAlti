"""media-ingest service on :8030 (SPEC §6.8).

The lat/lon -> cameras -> feeds pipeline as REST, for the frontend, harness,
vlm, and synthesis:

  GET  /api/health                          module status
  GET  /api/cameras                         all graph nodes (light form)
  GET  /api/camera/{cid}                    full node incl. bearing + live state
  GET  /api/nearby?lat=&lon=&radius_m=      cameras within radius, nearest first
  GET  /api/convergence?lat=&lon=&radius_m= CameraConvergence §6.7 (also ?street=)
  GET  /api/streets                         street -> camera-count index
  GET  /api/street/{name}                   ordered cameras on the street
  GET  /api/frame/{cid}/latest.jpg          newest frame (rate-gated upstream)
  GET  /api/frame/{cid}/record              its FrameRecord §6.1
  GET  /api/hls/{key}/{path}                HLS proxy (relative refs resolve here)
  GET  /api/satellite/{cid}?annotate=       satellite crop (arrows optional)
  POST /api/bearing/{cid} {bearing_deg}     manual FOV set (calibration UI)
  DELETE /api/bearing/{cid}                 clear manual, back to auto layers
  POST /api/orient/{cid}                    re-run orientation stack on one camera
  GET  /api/detections[/{cid}]              live object state per node
  POST /api/analyze/{cid}                   single-camera analysis now (3.1)
  POST /api/sweep/start | /api/sweep/stop   BFS traversal loop control
  GET  /api/sweep/status
  POST /api/priority {"camera_ids": [...]}  hot lane (module SPEC)
  GET  /api/tile/{z}/{x}/{y}                dark basemap proxy (cached)
  GET  /api/sat-tile/{z}/{x}/{y}            Esri satellite proxy (cached)
  GET  /api/buildings?s=&w=&n=&e=           OSM building footprints (3D view)

Run: uvicorn ingest.service:app --port 8030
"""
from __future__ import annotations

import asyncio
import json
import time

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import (activity, config, cvdetect, detect, feeds, netboot,
               observations, orientation, refuge, solar, vlm_forward)
from .graph import CameraGraph

app = FastAPI(title="GozAlti media-ingest")
app.add_middleware(CORSMiddleware, allow_origins=["*"],
                   allow_methods=["*"], allow_headers=["*"])

G = CameraGraph.load()
detect.load_persisted()
activity.register_saver(G.save)   # flags persist to the on-disk artifact
http_async = netboot.make_async_client()
_sweep_task: asyncio.Task | None = None


def _node_or_404(cid: str) -> dict:
    node = G.nodes.get(cid)
    if node is None:
        raise HTTPException(404, f"unknown camera id {cid!r}")
    return node


def _light(n: dict) -> dict:
    b = n.get("bearing") or {}
    live = detect.live_state(n["camera_id"])
    act = activity.effective_activity(n)
    cop = n.get("copresence")
    return {
        "activity": act,
        "active": act.get("active") if act else None,
        "last_activity_at": n.get("last_activity_at"),
        "last_person_at": cop.get("last_person_at") if cop else None,
        "street_context": n.get("street_context"),
        "camera_id": n["camera_id"], "key": n.get("key"),
        "lat": n["lat"], "lon": n["lon"],
        "desc": n.get("location_desc"), "street": n.get("street_name"),
        "neighborhood": n.get("neighborhood"), "ownership": n.get("ownership"),
        "has_stream": bool(n.get("has_stream")),
        "hls": f"/api/hls/{n['key']}/playlist.m3u8" if n.get("has_stream") else None,
        "snapshot": f"/api/frame/{n['camera_id']}/latest.jpg",
        "bearing_deg": b.get("bearing_deg"),
        "bearing_conf": b.get("bearing_conf"),
        "bearing_basis": b.get("basis"),
        "road_axis_deg": n.get("road_axis_deg"),
        "n_detections": len(live.get("detections", [])) if live.get("ok") else None,
        "dist_m": n.get("dist_m"),
    }


# ------------------------------------------------------------------- graph

@app.get("/api/health")
def api_health():
    return {"module": "media-ingest", "port": 8030,
            "graph": G.artifact["counts"], "sweep": detect.sweep_status()}


@app.get("/api/cameras")
def api_cameras(active_only: bool = False):
    out = [_light(n) for n in G.nodes.values()]
    return [c for c in out if c["active"]] if active_only else out


@app.get("/api/activity")
def api_activity():
    """Full-city activity map: which cameras show pixel-level motion right
    now. This is a pixel-change signal, not a people/safety signal."""
    return {cid: activity.effective_activity(n)
            for cid, n in G.nodes.items()}


@app.get("/api/camera/{cid}")
def api_camera(cid: str):
    node = _node_or_404(cid)
    return {**node, "live": detect.live_state(cid) or None,
            "links": _light(node)}


@app.get("/api/nearby")
def api_nearby(lat: float, lon: float, radius_m: float = 100.0,
               active_only: bool = False):
    cams = [_light(n) for n in G.nearby(lat, lon, radius_m)]
    if active_only:
        cams = [c for c in cams if c["active"]]
    return {"query": {"lat": lat, "lon": lon, "radius_m": radius_m},
            "street_near": G.street_near(lat, lon, max(radius_m, 150.0)),
            "cameras": cams}


@app.get("/api/convergence")
def api_convergence(lat: float | None = None, lon: float | None = None,
                    radius_m: float = 300.0, street: str | None = None):
    if street is None and (lat is None or lon is None):
        raise HTTPException(422, "give lat+lon or street")
    return G.convergence(lat, lon, radius_m, street)


@app.get("/api/streets")
def api_streets():
    return G.streets()


@app.get("/api/street/{name}")
def api_street(name: str):
    nodes = G.street(name)
    if not nodes:
        raise HTTPException(404, f"no cameras on street {name!r}")
    return [_light(n) for n in nodes]


# ------------------------------------------------------------------- frames

@app.get("/api/frame/{cid}/latest.jpg")
def api_frame(cid: str):
    _node_or_404(cid)
    blob = feeds.latest_frame_bytes(cid, G.nodes)
    if blob is None:
        raise HTTPException(502, "no frame available for this camera")
    return Response(content=blob, media_type="image/jpeg",
                    headers={"Cache-Control": "no-store"})


@app.get("/api/frame/{cid}/record")
def api_frame_record(cid: str):
    _node_or_404(cid)
    rec = feeds.latest_record(cid)
    if rec is None:
        raise HTTPException(404, "no FrameRecord yet for this camera")
    return rec


@app.get("/api/hls/{key}/{path:path}")
async def api_hls(key: str, path: str):
    url = f"{config.HLS_BASE}/{key}.stream/{path}"
    try:
        r = await http_async.get(url, timeout=20)
    except Exception as exc:
        raise HTTPException(502, f"upstream fetch failed: {exc}")
    if r.status_code != 200:
        raise HTTPException(r.status_code, "upstream error")
    ct = ("application/vnd.apple.mpegurl" if path.endswith(".m3u8")
          else "video/mp2t")
    return Response(content=r.content, media_type=ct,
                    headers={"Cache-Control": "no-store"})


# -------------------------------------------------------------- orientation

@app.get("/api/satellite/{cid}")
def api_satellite(cid: str, zoom: int = 18, annotate: bool = True):
    node = _node_or_404(cid)
    data = orientation.satellite_crop(node, zoom=min(max(zoom, 15), 19),
                                      annotate=annotate)
    if data is None:
        raise HTTPException(502, "satellite imagery unavailable")
    return Response(content=data, media_type="image/jpeg",
                    headers={"Cache-Control": "max-age=86400"})


class BearingBody(BaseModel):
    bearing_deg: float


@app.post("/api/bearing/{cid}")
def api_bearing_set(cid: str, body: BearingBody):
    node = _node_or_404(cid)
    rec = orientation.manual_set(cid, body.bearing_deg)
    node["bearing"] = {"bearing_deg": rec["bearing_deg"], "bearing_conf": 0.95,
                       "resolved": True, "basis": "manual-confirmed",
                       "layers": [{"layer": "manual", "ok": True, **rec}],
                       "computed_at": time.time()}
    G.save()
    return node["bearing"]


@app.delete("/api/bearing/{cid}")
def api_bearing_clear(cid: str):
    node = _node_or_404(cid)
    orientation.manual_clear(cid)
    orientation.satvlm_invalidate(cid)
    node["bearing"] = None
    G.save()
    return {"cleared": cid}


@app.post("/api/orient/{cid}")
def api_orient_one(cid: str):
    node = _node_or_404(cid)
    blob, _rec = feeds.latest_frame(node)
    node["bearing"] = orientation.resolve(node, blob, time.time())
    G.save()
    return node["bearing"]


# --------------------------------------------------------------- detections

@app.get("/api/detections")
def api_detections():
    return detect.live_state()


@app.get("/api/detections/{cid}")
def api_detections_one(cid: str):
    _node_or_404(cid)
    return detect.live_state(cid) or {}


@app.post("/api/analyze/{cid}")
def api_analyze(cid: str):
    _node_or_404(cid)
    res = detect.analyze_camera(G, cid)
    if res is None:
        raise HTTPException(502, "analysis failed")
    return res


# ------------------------------- consolidated router (modules/pathfinding)

import sys as _sys
_sys.path.insert(0, str(config.REPO_ROOT / "modules" / "pathfinding"))
import pathfind  # noqa: E402


@app.get("/api/route")
def api_route(olat: float, olon: float, dlat: float, dlon: float,
              kind: str = "safer", live: bool = True):
    """THE one-and-done pathfinding function (modules/pathfinding).
    Deterministic layers + cached OpenCV, returns immediately with
    live.incorporated=false; live=true (default) also starts the
    PathLiveSession that auto-replaces the path as VLM/CV/SDOT arrive —
    poll GET /api/route/live/{path_id}. Full cost model: risk_basis field."""
    try:
        if live:
            return pathfind.start_session(olat, olon, dlat, dlon, kind)
        return pathfind.find_path(olat, olon, dlat, dlon, kind)
    except ValueError as exc:
        raise HTTPException(422, {"error": str(exc)})


@app.get("/api/route/live/{path_id}")
def api_route_live(path_id: str, since: int | None = None):
    """Poll the live session: {version, changed_since, path}. ~2 s cadence
    is safe — all upstream fetching is gated inside the session loop."""
    s = pathfind.get_session(path_id)
    if s is None:
        raise HTTPException(404, "no such live session (expired after idle?)")
    return s.snapshot(since)


@app.delete("/api/route/live/{path_id}")
def api_route_live_stop(path_id: str):
    return {"stopped": pathfind.stop_session(path_id)}


# ------------------- per-segment LLM summaries (Ollama on the DGX Spark)

@app.post("/api/path/summaries")
def api_path_summaries_post(body: dict):
    """Queue/refresh LLM phrasings of per-segment evidence. Body:
    {path_id, segments:[{seg_key, ...evidence}]}. Coalesces on unchanged
    evidence; changed evidence re-queues and the old text serves with
    revising=true until replaced. pathfind live sessions enqueue
    automatically — this endpoint is for UI-supplied evidence (/api/path)."""
    from pathfind import summarize
    pid = body.get("path_id")
    if not pid:
        raise HTTPException(422, "path_id required")
    return summarize.enqueue(pid, body.get("segments", []))


@app.get("/api/path/summaries/{path_id}")
def api_path_summaries_get(path_id: str):
    """Poll summaries: {available, why, model, pending, summaries}."""
    from pathfind import summarize
    return summarize.get(path_id)


@app.get("/api/path/summaries")
def api_path_summaries_status():
    from pathfind import summarize
    return summarize.status()


# --------------------------------------------- evidence-enriched pathfinding

@app.get("/api/path")
def api_path(olat: float, olon: float, dlat: float, dlon: float,
             kind: str = "safer"):
    """Two points -> harness A* route (risk-weighted natively) enriched with
    this module's live evidence per segment: camera coverage + activity,
    co-presence, lighting + sun, open refuges. See pathrisk.RISK_FORMULA."""
    from . import pathrisk
    try:
        return pathrisk.route_enriched(G, olat, olon, dlat, dlon, kind)
    except pathrisk.harness.RouteError as exc:
        raise HTTPException(422, {"error": exc.code, **exc.detail})


# ---------------------------------------------- local OpenCV CNN pipeline

@app.get("/api/cv/status")
def api_cv_status():
    """Local CNN readiness (yolov4-tiny via opencv-dnn, worker processes)."""
    return cvdetect.status()


@app.get("/api/cv/camera/{cid}")
def api_cv_camera(cid: str, force: bool = False, backend: str | None = None):
    """Single-camera local pipeline: freshest frame (live streamer / gated
    fetch) -> CNN -> mathematics layer -> world-positioned detections.
    Polling faster than new frames arrive returns the cached result
    (`cached: true`) with zero upstream requests. `backend=detlib` forces
    an HQ pass through Adi's stack regardless of the auto policy."""
    if backend not in (None, "detlib", "yolo"):
        raise HTTPException(422, "backend must be detlib or yolo")
    node = _node_or_404(cid)
    from . import stream
    stream.ensure(node)   # the focused camera may evict an LRU streamer
    return cvdetect.analyze_camera_cv(node, force=force, backend=backend)


@app.get("/api/cv/point")
def api_cv_point(lat: float, lon: float, radius_m: float = 150.0):
    """Point pipeline: lat/lon -> cameras that see it -> parallel frames
    (rate gates hold) -> parallel CNN forward passes -> world positions."""
    return cvdetect.analyze_point_cv(G, lat, lon, radius_m)


@app.get("/api/sun")
def api_sun(lat: float = 47.61, lon: float = -122.33):
    """Deterministic solar position right now (NOAA algorithm)."""
    az, el = solar.solar_position(lat, lon, time.time())
    return {"azimuth_deg": round(az, 1), "elevation_deg": round(el, 1),
            "is_daylight": el > 0, "basis": "noaa-solar-position"}


@app.get("/api/refuge")
def api_refuge(lat: float, lon: float, radius_m: float = 150.0):
    """Open businesses to duck into near a point. Scope is honest: counts
    cover OSM places with a known opening_hours tag, evaluated live in
    Seattle time; unparseable hours count as unknown, not closed."""
    return refuge.near(lat, lon, radius_m)


@app.get("/api/refuge/bbox")
def api_refuge_bbox(s: float, w: float, n: float, e: float):
    return refuge.in_bbox(s, w, n, e)


@app.get("/api/refuge/street/{name}")
def api_refuge_street(name: str):
    nodes = G.street(name)
    if not nodes:
        raise HTTPException(404, f"no cameras on street {name!r}")
    return refuge.along_street(nodes)


@app.get("/api/context/{cid}")
def api_context(cid: str):
    """CameraContext — everything this module knows about one camera, for
    the VLM/VSS side and synthesis. Every field carries its basis/source.
    (Module-internal doc; graduating it into god-spec §6 is a team edit.)"""
    node = _node_or_404(cid)
    b = node.get("bearing") or {}
    az, el = solar.solar_position(node["lat"], node["lon"], time.time())
    return {
        "camera_id": cid,
        "key": node.get("key"),
        "lat": node["lat"], "lon": node["lon"],
        "location_desc": node.get("location_desc"),
        "street": node.get("street_name"),
        "neighborhood": node.get("neighborhood"),
        "has_stream": bool(node.get("has_stream")),
        "frame": feeds.latest_record(cid),                     # FrameRecord §6.1
        "bearing": node.get("bearing"),
        "activity": activity.effective_activity(node),         # pixel signal only
        "last_activity_at": node.get("last_activity_at"),
        "copresence": node.get("copresence"),                  # last person in view
        "street_context": node.get("street_context"),          # structural OSM facts
        "refuge": refuge.near(node["lat"], node["lon"], 150.0),
        "sun": {"azimuth_deg": round(az, 1), "elevation_deg": round(el, 1),
                "is_daylight": el > 0, "basis": "noaa-solar-position"},
        "detections": detect.live_state(cid) or None,
        "prior_observations": observations.priors(cid),
    }


@app.post("/api/read/{cid}")
def api_read(cid: str):
    """Hot-lane push to the vlm module (:8040/read) with temporal
    breadcrumbs. Needs VLM_READ_URL configured."""
    _node_or_404(cid)
    if not vlm_forward.enabled():
        raise HTTPException(503, "VLM_READ_URL not configured")
    obs = vlm_forward.read_camera(G.nodes[cid])
    if obs is None:
        raise HTTPException(502, "no frame or vlm read failed")
    return obs


@app.get("/api/observations/{cid}")
def api_observations(cid: str):
    """The breadcrumb ring buffer for a camera (verbatim Observations)."""
    _node_or_404(cid)
    return observations.priors(cid)


class PriorityBody(BaseModel):
    camera_ids: list[str]


@app.post("/api/priority")
def api_priority(body: PriorityBody):
    return {"focus": detect.set_focus(body.camera_ids)}


@app.post("/api/sweep/start")
async def api_sweep_start():
    global _sweep_task
    if _sweep_task and not _sweep_task.done():
        return detect.sweep_status()
    _sweep_task = asyncio.create_task(detect.run_sweep_loop(G))
    return detect.sweep_status()


@app.post("/api/sweep/stop")
async def api_sweep_stop():
    detect.stop_sweep()
    return detect.sweep_status()


@app.get("/api/sweep/status")
def api_sweep_status():
    return detect.sweep_status()


# -------------------------------------------------------------------- tiles

async def _cached_tile(cache_dir, name: str, url: str, media: str):
    p = cache_dir / name
    if p.exists():
        return FileResponse(p, media_type=media)
    try:
        r = await http_async.get(url, timeout=20)
        r.raise_for_status()
    except Exception as exc:
        raise HTTPException(502, f"tile fetch failed: {exc}")
    p.write_bytes(r.content)
    return Response(content=r.content, media_type=media)


@app.get("/api/tile/{z}/{x}/{y}")
async def api_tile(z: int, x: int, y: int):
    return await _cached_tile(config.TILES, f"{z}_{x}_{y}.png",
                              config.DARK_TILE.format(z=z, x=x, y=y), "image/png")


@app.get("/api/sat-tile/{z}/{x}/{y}")
async def api_sat_tile(z: int, x: int, y: int):
    return await _cached_tile(config.SAT_TILES, f"{z}_{x}_{y}.jpg",
                              config.ESRI_TILE.format(z=z, y=y, x=x), "image/jpeg")


@app.get("/api/buildings")
async def api_buildings(s: float, w: float, n: float, e: float):
    """OSM building footprints for the bbox as GeoJSON — the 3D extrusion
    layer in the test UI. Cached per rounded bbox."""
    key = f"bld_{s:.3f}_{w:.3f}_{n:.3f}_{e:.3f}.json"
    p = config.TILES / key
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    if (n - s) > 0.03 or (e - w) > 0.04:
        raise HTTPException(422, "bbox too large; zoom in before loading 3D")
    q = (f'[out:json][timeout:60];way["building"]({s},{w},{n},{e});out geom;')
    try:
        r = await http_async.post(config.OVERPASS, data={"data": q}, timeout=90)
        r.raise_for_status()
        osm = r.json()
    except Exception as exc:
        raise HTTPException(502, f"overpass failed: {exc}")
    feats = []
    for el in osm.get("elements", []):
        geom = el.get("geometry")
        if not geom or len(geom) < 3:
            continue
        try:
            levels = float(el.get("tags", {}).get("building:levels", "2"))
        except ValueError:
            levels = 2.0
        try:
            height = float(str(el.get("tags", {}).get("height", "")).split()[0])
        except (ValueError, IndexError):
            height = levels * 3.2
        feats.append({
            "type": "Feature",
            "properties": {"height": height},
            "geometry": {"type": "Polygon",
                         "coordinates": [[[g["lon"], g["lat"]] for g in geom]]},
        })
    gj = {"type": "FeatureCollection", "features": feats}
    p.write_text(json.dumps(gj), encoding="utf-8")
    return gj


# ------------------------------------------------- optional test-UI mounting

_UI_DIR = config.REPO_ROOT / "experiments" / "berkan_testing"
if _UI_DIR.exists():
    @app.get("/")
    def index():
        return FileResponse(_UI_DIR / "index.html")

    app.mount("/static", StaticFiles(directory=_UI_DIR), name="static")
