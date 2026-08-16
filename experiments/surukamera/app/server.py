"""M4/M5 backend: FastAPI on localhost:8000.

Serves the frontend, the street/pair/frame APIs, a rate-limited snapshot
proxy, a rewrite-free HLS proxy (Wowza chunklists use relative URLs), and a
cached dark-basemap tile proxy. All upstream traffic goes through the
in-process DNS-over-TCP proxy (app.netboot) because this machine's network
blocks UDP/53.

Run: python -m uvicorn app.server:app --port 8000
"""
from __future__ import annotations

import asyncio
import json
import time
from collections import deque
from dataclasses import asdict
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from pydantic import BaseModel

from app import bearing, geometry, netboot, pairs

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
WEB = ROOT / "web"
TILE_CACHE = ROOT / "cache" / "tiles"
TILE_CACHE.mkdir(parents=True, exist_ok=True)

HLS_BASE = "https://61e0c5d388c2e.streamlock.net:443/live"
TILE_URL = "https://basemaps.cartocdn.com/dark_all/{z}/{x}/{y}.png"

app = FastAPI(title="SuruKamera")

STREETS = json.loads((DATA / "streets.json").read_text(encoding="utf-8"))
CAMS: dict[str, dict] = STREETS["cameras"]
PAIRS: list[dict] = STREETS["pairs"]

# Warm cache from the M3 sweep, if it ran
PAIR_REPORT: dict = {}
if (DATA / "pair_report.json").exists():
    PAIR_REPORT = json.loads((DATA / "pair_report.json").read_text(encoding="utf-8"))

# geometry history per camera for PTZ-drift detection (last 2 samples)
HISTORY: dict[str, deque] = {}
GEOM_LOCK = asyncio.Lock()

http_async = netboot.make_async_client()
http_sync = netboot.make_client()


# ------------------------------------------------------------------ helpers

def _street_summaries() -> list[dict]:
    best: dict[str, float] = {}
    layouts: dict[str, int] = {}
    for row in PAIR_REPORT.get("pairs", []):
        s = row["street"]
        best[s] = max(best.get(s, 0.0), row["decision"]["score"] or 0.0)
        if row["decision"]["layout"] == "STACKED_CONTINUITY":
            layouts[s] = layouts.get(s, 0) + 1
    out = []
    for name, cam_ids in STREETS["street_index"].items():
        street_pairs = [p for p in PAIRS if p["street"] == name]
        if not street_pairs:
            continue
        out.append({
            "name": name,
            "cameras": len(cam_ids),
            "pairs": len(street_pairs),
            "best_score": best.get(name),
            "stackable_pairs": layouts.get(name, 0),
        })
    out.sort(key=lambda s: (-(s["best_score"] or 0), -s["cameras"]))
    return out


def _compute_geometry_sync(cid: str) -> geometry.ViewGeometry:
    vg = geometry.compute_view_geometry(CAMS[cid], http_sync)
    hist = HISTORY.setdefault(cid, deque(maxlen=2))
    if not hist or hist[-1].image_hash != vg.image_hash:
        hist.append(vg)
    return vg


def _pane(cid: str, vg: geometry.ViewGeometry) -> dict:
    cam = CAMS[cid]
    return {
        "camera_id": cid,
        "key": cam["key"],
        "desc": cam["location_desc"],
        "mode": "stream" if cam.get("has_stream") else "snapshot",
        "hls": f"/api/hls/{cam['key']}/playlist.m3u8" if cam.get("has_stream") else None,
        "snapshot": f"/api/snapshot/{cid}",
        "lat": cam["lat"], "lon": cam["lon"],
        "fetched_at": vg.fetched_at,
        "geometry": asdict(vg),
    }


# --------------------------------------------------------------------- APIs

@app.get("/api/streets")
def api_streets():
    return _street_summaries()


@app.get("/api/cameras")
def api_cameras():
    return [
        {
            "camera_id": c["camera_id"], "key": c["key"], "lat": c["lat"],
            "lon": c["lon"], "desc": c["location_desc"], "street": c["street_name"],
            "has_stream": bool(c.get("has_stream")),
            "road_axis_deg": c.get("road_axis_deg"),
        }
        for c in CAMS.values()
    ]


@app.get("/api/street/{name}/pairs")
def api_street_pairs(name: str):
    rows = [p for p in PAIRS if p["street"] == name]
    if not rows:
        raise HTTPException(404, f"no pairs for street {name!r}")
    cached = {(r["a"], r["b"]): r["decision"] for r in PAIR_REPORT.get("pairs", [])}
    return [
        {**p, "cached_decision": cached.get((p["a"], p["b"])),
         "a_desc": CAMS[p["a"]]["location_desc"],
         "b_desc": CAMS[p["b"]]["location_desc"]}
        for p in rows
    ]


@app.get("/api/pair/{a}/{b}/frames")
async def api_pair_frames(a: str, b: str):
    if a not in CAMS or b not in CAMS:
        raise HTTPException(404, "unknown camera id")
    pair_meta = next((p for p in PAIRS if {p["a"], p["b"]} == {a, b}), None)
    gap_m = pair_meta["gap_m"] if pair_meta else None

    loop = asyncio.get_event_loop()
    async with GEOM_LOCK:  # one geometry computation at a time keeps CPU sane
        gA, gB = await asyncio.gather(
            loop.run_in_executor(None, _compute_geometry_sync, a),
            loop.run_in_executor(None, _compute_geometry_sync, b),
        )

    hist_a, hist_b = HISTORY.get(a, []), HISTORY.get(b, [])
    stale_a = pairs.is_stale(hist_a[0] if len(hist_a) > 1 else None, gA)
    stale_b = pairs.is_stale(hist_b[0] if len(hist_b) > 1 else None, gB)

    decision = pairs.classify_pair(CAMS[a], CAMS[b], gA, gB,
                                   gap_m or 150.0, stale_a, stale_b)

    panes: dict[str, dict] = {}
    if decision.layout == "STACKED_CONTINUITY":
        panes["top"] = _pane(decision.downstream,
                             gA if decision.downstream == a else gB)
        panes["bottom"] = _pane(decision.upstream,
                                gA if decision.upstream == a else gB)
    else:
        panes["left"] = _pane(a, gA)
        panes["right"] = _pane(b, gB)

    return {
        "street": pair_meta["street"] if pair_meta else None,
        "gap_m": gap_m,
        "a": a, "b": b,
        "decision": asdict(decision),
        "panes": panes,
        "server_time": time.time(),
    }


@app.get("/api/snapshot/{cid}")
def api_snapshot(cid: str):
    if cid not in CAMS:
        raise HTTPException(404, "unknown camera id")
    data, fetched_at, _ = geometry.fetch_snapshot(CAMS[cid], http_sync)
    return Response(content=data, media_type="image/jpeg", headers={
        "Cache-Control": "no-store",
        "X-Fetched-At": f"{fetched_at:.0f}",
    })


@app.get("/api/hls/{key}/{path:path}")
async def api_hls(key: str, path: str):
    """Proxy Wowza HLS. Chunklist/segment refs are relative, so they resolve
    back to this route with no rewriting."""
    url = f"{HLS_BASE}/{key}.stream/{path}"
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


@app.get("/api/satellite/{cid}")
def api_satellite(cid: str, zoom: int = 18):
    """Annotated satellite crop for a camera: arrows A/B mark the two
    axis-direction hypotheses. Used by the manual-confirm UI and the VLM."""
    if cid not in CAMS:
        raise HTTPException(404, "unknown camera id")
    data = bearing.satellite_crop(CAMS[cid], zoom=min(max(zoom, 15), 19),
                                  client=http_sync)
    if data is None:
        raise HTTPException(502, "satellite imagery unavailable")
    return Response(content=data, media_type="image/png",
                    headers={"Cache-Control": "max-age=86400"})


class BearingBody(BaseModel):
    bearing_deg: float


@app.post("/api/bearing/{cid}")
def api_bearing_set(cid: str, body: BearingBody):
    """Manual bearing override (L0 of the bearing stack)."""
    if cid not in CAMS:
        raise HTTPException(404, "unknown camera id")
    rec = bearing.manual_set(cid, body.bearing_deg)
    # invalidate this camera's geometry history so the next fetch re-resolves
    HISTORY.pop(cid, None)
    for f in geometry.GEOM_CACHE.glob(f"v2_{cid}_*.json"):
        f.unlink(missing_ok=True)
    return rec


@app.delete("/api/bearing/{cid}")
def api_bearing_clear(cid: str):
    if cid not in CAMS:
        raise HTTPException(404, "unknown camera id")
    bearing.manual_clear(cid)
    HISTORY.pop(cid, None)
    for f in geometry.GEOM_CACHE.glob(f"v2_{cid}_*.json"):
        f.unlink(missing_ok=True)
    return {"cleared": cid}


SAT_TILE_CACHE = ROOT / "cache" / "sat_tiles"
SAT_TILE_CACHE.mkdir(parents=True, exist_ok=True)


@app.get("/api/sat-tile/{z}/{x}/{y}")
async def api_sat_tile(z: int, x: int, y: int):
    """Esri World Imagery tile proxy (satellite basemap toggle)."""
    p = SAT_TILE_CACHE / f"{z}_{x}_{y}.jpg"
    if p.exists():
        return FileResponse(p, media_type="image/jpeg")
    url = bearing.ESRI_TILE.format(z=z, y=y, x=x)
    try:
        r = await http_async.get(url, timeout=20)
        r.raise_for_status()
    except Exception as exc:
        raise HTTPException(502, f"satellite tile fetch failed: {exc}")
    p.write_bytes(r.content)
    return Response(content=r.content, media_type="image/jpeg")


@app.get("/api/tile/{z}/{x}/{y}")
async def api_tile(z: int, x: int, y: int):
    p = TILE_CACHE / f"{z}_{x}_{y}.png"
    if p.exists():
        return FileResponse(p, media_type="image/png")
    url = TILE_URL.format(z=z, x=x, y=y)
    try:
        r = await http_async.get(url, timeout=20)
        r.raise_for_status()
    except Exception as exc:
        raise HTTPException(502, f"tile fetch failed: {exc}")
    p.write_bytes(r.content)
    return Response(content=r.content, media_type="image/png")


@app.get("/")
def index():
    return FileResponse(WEB / "index.html")


app.mount("/static", StaticFiles(directory=WEB), name="static")
