"""Step 2 — camera orientation precompute (adapted from surukamera's
7-layer bearing stack, minus optical flow).

The road AXIS comes from the OSM snap (high confidence, mod 180). The layers
below decide WHICH of the two 180-deg directions the camera faces:

  L0 manual       operator confirmed via the calibration UI          0.95
  L1 sat-vlm      VLM reconciles frame vs annotated satellite crop   0.55-0.80
  L2 corner-token key suffix NWC/NEC/SWC/SEC (pole corner)           0.50
  L3 sun-history  per-camera overexposure peak vs solar azimuth      0.55
  L4 sun-instant  glare / sky-brightness asymmetry vs sun position   0.35-0.50
  (fallback)      axis-only, direction unresolved                    0.35

Every layer's verdict is kept on the node so the UI can always show WHY a
bearing was chosen — never an unexplained number (SPEC §1).

Precompute pass: python -m ingest.orientation [--limit N]
"""
from __future__ import annotations

import json
import math
import sys
import threading
import time
from pathlib import Path

import cv2
import numpy as np

from . import config, feeds, netboot, solar, vlm_client
from .graph import CameraGraph

_LOCK = threading.Lock()


def ang_diff(a: float, b: float) -> float:
    d = abs(a - b) % 360.0
    return d if d <= 180.0 else 360.0 - d


def _signed_diff(a: float, b: float) -> float:
    d = (a - b) % 360.0
    return d - 360.0 if d > 180.0 else d


def _load_json(path: Path) -> dict:
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}
    return {}


# ------------------------------------------------------------- manual store

def manual_get(camera_id: str) -> dict | None:
    return _load_json(config.MANUAL_BEARINGS).get(camera_id)


def manual_set(camera_id: str, bearing_deg: float) -> dict:
    with _LOCK:
        d = _load_json(config.MANUAL_BEARINGS)
        d[camera_id] = {"bearing_deg": round(bearing_deg % 360.0, 1),
                        "source": "manual",
                        "set_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
        config.MANUAL_BEARINGS.write_text(json.dumps(d, indent=1), encoding="utf-8")
        return d[camera_id]


def manual_clear(camera_id: str) -> None:
    with _LOCK:
        d = _load_json(config.MANUAL_BEARINGS)
        if camera_id in d:
            del d[camera_id]
            config.MANUAL_BEARINGS.write_text(json.dumps(d, indent=1), encoding="utf-8")


# --------------------------------------------------------- satellite imagery

def _tile_xy(lat: float, lon: float, z: int) -> tuple[float, float]:
    n = 2 ** z
    x = (lon + 180.0) / 360.0 * n
    lat_r = math.radians(lat)
    y = (1.0 - math.asinh(math.tan(lat_r)) / math.pi) / 2.0 * n
    return x, y


def satellite_crop(cam: dict, zoom: int = 18, size: int = 512,
                   annotate: bool = True, client=None) -> bytes | None:
    """Esri World Imagery crop centered on the camera, with the two
    axis-hypothesis arrows drawn (A = way direction, B = opposite).
    JPEG bytes, cached on disk — the satellite doesn't move."""
    cache = config.SATELLITE / f"{cam['camera_id']}_{zoom}_{int(annotate)}.jpg"
    if cache.exists():
        return cache.read_bytes()

    close = False
    if client is None:
        client = netboot.make_client()
        close = True
    try:
        fx, fy = _tile_xy(cam["lat"], cam["lon"], zoom)
        cx, cy = int(fx), int(fy)
        rows = []
        for ty in (cy - 1, cy, cy + 1):
            row = []
            for tx in (cx - 1, cx, cx + 1):
                r = client.get(config.ESRI_TILE.format(z=zoom, y=ty, x=tx), timeout=15)
                r.raise_for_status()
                img = cv2.imdecode(np.frombuffer(r.content, np.uint8), cv2.IMREAD_COLOR)
                if img is None:
                    return None
                row.append(img)
            rows.append(np.hstack(row))
        mosaic = np.vstack(rows)  # 768x768, camera inside center tile

        px = int((fx - (cx - 1)) * 256)
        py = int((fy - (cy - 1)) * 256)
        half = size // 2
        x0, y0 = max(0, px - half), max(0, py - half)
        crop = mosaic[y0:y0 + size, x0:x0 + size].copy()
        ccx, ccy = px - x0, py - y0

        if annotate:
            axis = cam.get("way_dir_deg") or cam.get("road_axis_deg") or 0.0
            for label, bearing, color in (("A", axis, (80, 220, 80)),
                                          ("B", (axis + 180) % 360, (60, 140, 245))):
                rad = math.radians(bearing)
                ex = int(ccx + 150 * math.sin(rad))
                ey = int(ccy - 150 * math.cos(rad))
                cv2.arrowedLine(crop, (ccx, ccy), (ex, ey), color, 3, tipLength=0.18)
                lx = int(ccx + 175 * math.sin(rad)) - 8
                ly = int(ccy - 175 * math.cos(rad)) + 8
                cv2.putText(crop, label, (lx, ly), cv2.FONT_HERSHEY_SIMPLEX,
                            0.9, color, 2, cv2.LINE_AA)
            cv2.circle(crop, (ccx, ccy), 6, (60, 220, 245), -1)

        ok, buf = cv2.imencode(".jpg", crop, [cv2.IMWRITE_JPEG_QUALITY, 90])
        if not ok:
            return None
        data = buf.tobytes()
        cache.write_bytes(data)
        return data
    except Exception:
        return None
    finally:
        if close:
            client.close()


# ----------------------------------------------------------- sun/shadow layers

def sun_instant(frame_bytes: bytes, fetched_at: float, lat: float, lon: float,
                d0: float, d1: float) -> dict | None:
    """Break the 180-deg tie from the current frame's glare / sky-brightness
    asymmetry vs the exact solar position. Time-of-day aware by construction:
    the sun's azimuth at fetch time is the reference the shadows/glare are
    compared against. Only fires when the evidence is unambiguous."""
    sun_az, sun_el = solar.solar_position(lat, lon, fetched_at)
    if sun_el < 8.0:
        return None  # night / dusk: no shadow signal

    img = cv2.imdecode(np.frombuffer(frame_bytes, np.uint8), cv2.IMREAD_GRAYSCALE)
    if img is None:
        return None
    h, w = img.shape
    upper = img[: int(0.35 * h), :]

    rel0 = _signed_diff(sun_az, d0)
    rel1 = _signed_diff(sun_az, d1)

    glare_frac = float((upper > 245).mean())
    if glare_frac > 0.10:
        pick, rel = (d0, rel0) if abs(rel0) < abs(rel1) else (d1, rel1)
        if abs(rel) < 75.0:
            conf = 0.5 if sun_el < 30.0 else 0.4
            return {"bearing_deg": pick, "conf": conf, "kind": "glare",
                    "sun_az": round(sun_az, 1), "sun_el": round(sun_el, 1),
                    "glare_frac": round(glare_frac, 3)}
        return None

    third = w // 3
    left = float(upper[:, :third].mean())
    right = float(upper[:, -third:].mean())
    grad = (right - left) / max(left, right, 1.0)
    if abs(grad) < 0.10:
        return None
    bright_side = 1.0 if grad > 0 else -1.0
    matches = []
    for d, rel in ((d0, rel0), (d1, rel1)):
        if abs(rel) < 85.0 and math.copysign(1.0, rel) == bright_side:
            matches.append(d)
    if len(matches) == 1:
        return {"bearing_deg": matches[0], "conf": 0.35, "kind": "sky-asymmetry",
                "sun_az": round(sun_az, 1), "sun_el": round(sun_el, 1),
                "grad": round(grad, 3)}
    return None


def sun_history(camera_id: str, lat: float, lon: float,
                d0: float, d1: float) -> dict | None:
    """Overexposure-vs-time-of-day from our frames archive: the frame with
    peak glare marks the hour the camera faced the sun. Strengthens
    automatically as the sweep cache grows."""
    d = config.FRAMES / camera_id
    if not d.exists():
        return None
    samples = []
    for p in sorted(d.glob("*.jpg")):
        try:
            ts = time.mktime(time.strptime(p.stem, "%Y%m%dT%H%M%SZ")) - time.timezone
        except ValueError:
            continue
        img = cv2.imdecode(np.frombuffer(p.read_bytes(), np.uint8), cv2.IMREAD_GRAYSCALE)
        if img is None:
            continue
        upper = img[: int(0.35 * img.shape[0]), :]
        samples.append((ts, float((upper > 245).mean())))
    if len(samples) < 10:
        return None
    span_h = (max(s[0] for s in samples) - min(s[0] for s in samples)) / 3600.0
    if span_h < 4.0:
        return None
    over = np.array([s[1] for s in samples])
    peak_i = int(np.argmax(over))
    if over[peak_i] < 0.08 or over[peak_i] < 2.5 * float(np.median(over)):
        return None
    peak_az, peak_el = solar.solar_position(lat, lon, samples[peak_i][0])
    if peak_el < 5.0:
        return None
    pick = d0 if ang_diff(peak_az, d0) < ang_diff(peak_az, d1) else d1
    if ang_diff(peak_az, pick) > 75.0:
        return None
    return {"bearing_deg": pick, "conf": 0.55, "kind": "overexposure-peak",
            "peak_sun_az": round(peak_az, 1), "n_samples": len(samples),
            "span_h": round(span_h, 1)}


# ----------------------------------------------------- key-token heuristics

CORNER_BEARING = {"NWC": 135.0, "NEC": 225.0, "SEC": 315.0, "SWC": 45.0}


def corner_token_bearing(cam: dict) -> float | None:
    """SDOT keys sometimes end in the pole's intersection corner
    (e.g. Terry_Thomas_NWC); the camera points across the intersection,
    away from its corner. Coarse (+/-45), snapped to axis."""
    key = (cam.get("key") or "").upper()
    for tok, b in CORNER_BEARING.items():
        if key.endswith("_" + tok):
            return b
    return None


def axis_token_consistent(cam: dict) -> bool | None:
    """NS/EW key suffix vs OSM snap axis. None = no token."""
    key = (cam.get("key") or "").upper()
    tok = "NS" if key.endswith("_NS") else "EW" if key.endswith("_EW") else None
    if tok is None or cam.get("road_axis_deg") is None:
        return None
    axis = cam["road_axis_deg"] % 180.0
    is_ns = axis < 45.0 or axis > 135.0
    return is_ns if tok == "NS" else not is_ns


# ------------------------------------------------------------------- fusion

def _satvlm_cached(camera_id: str) -> dict | None:
    return _load_json(config.SATVLM_CACHE).get(camera_id)


def _satvlm_store(camera_id: str, result: dict | None) -> None:
    with _LOCK:
        d = _load_json(config.SATVLM_CACHE)
        d[camera_id] = result or {"none": True, "at": time.time()}
        config.SATVLM_CACHE.write_text(json.dumps(d, indent=1), encoding="utf-8")


def satvlm_invalidate(camera_id: str) -> None:
    with _LOCK:
        d = _load_json(config.SATVLM_CACHE)
        if camera_id in d:
            del d[camera_id]
            config.SATVLM_CACHE.write_text(json.dumps(d, indent=1), encoding="utf-8")


def resolve(cam: dict, frame_bytes: bytes | None, fetched_at: float,
            client=None) -> dict:
    """Fuse all layers into a bearing block:
    {bearing_deg, bearing_conf, resolved, basis, layers, computed_at}."""
    way_dir = cam.get("way_dir_deg")
    if way_dir is None:
        return {"bearing_deg": cam.get("road_axis_deg"), "bearing_conf": 0.2,
                "resolved": False, "basis": "no-map", "layers": [],
                "computed_at": time.time()}
    d0, d1 = way_dir % 360.0, (way_dir + 180.0) % 360.0
    layers: list[dict] = []

    def snap(bearing: float) -> float | None:
        cand = d0 if ang_diff(bearing, d0) <= ang_diff(bearing, d1) else d1
        return cand if ang_diff(bearing, cand) < 55.0 else None

    def done(bearing: float, conf: float, basis: str) -> dict:
        out = {"bearing_deg": bearing, "bearing_conf": conf, "resolved": True,
               "basis": basis, "layers": layers, "computed_at": time.time()}
        if axis_token_consistent(cam) is False:
            layers.append({"layer": "axis-token", "ok": False,
                           "why": "key NS/EW suffix disagrees with OSM snap axis"})
            out["bearing_conf"] = round(conf * 0.8, 3)
            out["basis"] += "+axis-token-mismatch"
        return out

    # L0 manual
    m = manual_get(cam["camera_id"])
    if m:
        # manual overrides are exact — no axis snapping, the operator saw the view
        layers.append({"layer": "manual", "ok": True, **m})
        return done(m["bearing_deg"], 0.95, "manual-confirmed")

    # L1 satellite VLM reconciliation (static per camera — cached persistently)
    if vlm_client.available() and frame_bytes:
        cached = _satvlm_cached(cam["camera_id"])
        if cached is None:
            sat = satellite_crop(cam, client=client)
            res = (vlm_client.satellite_cross_check(frame_bytes, sat, d0, d1)
                   if sat else None)
            _satvlm_store(cam["camera_id"], res)
            cached = res or {"none": True}
        if cached and not cached.get("none"):
            b = snap(cached["bearing_deg"])
            layers.append({"layer": "sat-vlm", "ok": b is not None, **cached})
            if b is not None:
                conf = {"high": 0.8, "medium": 0.7, "low": 0.55}.get(
                    cached.get("confidence", "low"), 0.55)
                return done(b, conf, f"satellite-vlm(arrow {cached['arrow']})")

    # L2 corner token
    ct = corner_token_bearing(cam)
    if ct is not None:
        b = snap(ct)
        layers.append({"layer": "corner-token", "ok": b is not None,
                       "raw_bearing": ct})
        if b is not None:
            return done(b, 0.5, "corner-token(key suffix)")

    # L3 sun history
    sh = sun_history(cam["camera_id"], cam["lat"], cam["lon"], d0, d1)
    if sh:
        layers.append({"layer": "sun-history", "ok": True, **sh})
        return done(sh["bearing_deg"], sh["conf"], "sun-history(overexposure peak)")

    # L4 sun instant (needs the frame + the solar position right now)
    if frame_bytes:
        si = sun_instant(frame_bytes, fetched_at, cam["lat"], cam["lon"], d0, d1)
        if si:
            layers.append({"layer": "sun-instant", "ok": True, **si})
            return done(si["bearing_deg"], si["conf"], f"sun-{si['kind']}")

    layers.append({"layer": "fallback", "ok": False,
                   "why": "no layer resolved direction"})
    return {"bearing_deg": d0, "bearing_conf": 0.35, "resolved": False,
            "basis": "axis-only", "layers": layers, "computed_at": time.time()}


# --------------------------------------------------------------- precompute

def precompute(g: CameraGraph, camera_ids: list[str] | None = None,
               log=print) -> dict:
    """Go camera by camera (BFS order), grab a current frame under the rate
    gates, resolve orientation, store the bearing block on the node, save."""
    ids = camera_ids or [c for c in g.bfs() if g.nodes[c].get("street_name")]
    counts = {"resolved": 0, "axis_only": 0, "no_frame": 0}
    client = netboot.make_client()
    for i, cid in enumerate(ids):
        node = g.nodes[cid]
        blob, rec = feeds.latest_frame(node)
        fetched_at = time.time()
        if blob is None:
            counts["no_frame"] += 1
        bearing = resolve(node, blob, fetched_at, client=client)
        node["bearing"] = bearing
        counts["resolved" if bearing["resolved"] else "axis_only"] += 1
        if (i + 1) % 25 == 0:
            g.save()
            log(f"[orient] {i + 1}/{len(ids)} {counts}")
    g.save()
    client.close()
    return counts


if __name__ == "__main__":
    limit = None
    if "--limit" in sys.argv:
        limit = int(sys.argv[sys.argv.index("--limit") + 1])
    graph = CameraGraph.load()
    todo = [c for c in graph.bfs() if graph.nodes[c].get("street_name")][:limit]
    print(json.dumps(precompute(graph, todo), indent=2))
