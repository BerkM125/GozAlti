"""Layered camera-bearing resolution.

The road AXIS always comes from the OSM snap (high confidence, mod 180).
These layers decide WHICH of the two 180-deg directions the camera faces,
in priority order:

  L0  manual        operator confirmed via UI (8-direction buttons)   0.95
  L1  oneway+flow   optical flow + mapped travel direction            <=0.90
  L2  sat-vlm       VLM matches frame against annotated satellite     0.80
  L3  landmark-vlm  VLM spots Space Needle / Rainier / etc in frame   0.75
  L4  sun-history   per-camera overexposure peak vs solar azimuth     0.55
  L5  sun-instant   glare / sky-brightness asymmetry vs sun position  0.35-0.50
  (fallback)        unresolved -> pair-level hypothesis testing       0.35

Every layer's verdict is kept in the result for the diagnostics rail, so
the UI can always show WHY a bearing was chosen.
"""
from __future__ import annotations

import json
import math
import threading
import time
from pathlib import Path

import cv2
import numpy as np

from app import netboot, solar, vlm

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
SNAP_CACHE = ROOT / "cache" / "snapshots"
SAT_CACHE = ROOT / "cache" / "satellite"
SAT_CACHE.mkdir(parents=True, exist_ok=True)

MANUAL_FILE = DATA / "manual_bearings.json"
SATVLM_FILE = DATA / "satellite_vlm.json"
_LOCK = threading.Lock()

ESRI_TILE = ("https://server.arcgisonline.com/ArcGIS/rest/services/"
             "World_Imagery/MapServer/tile/{z}/{y}/{x}")


def ang_diff(a: float, b: float) -> float:
    d = abs(a - b) % 360.0
    return d if d <= 180.0 else 360.0 - d


def _signed_diff(a: float, b: float) -> float:
    """a - b wrapped to (-180, 180]."""
    d = (a - b) % 360.0
    return d - 360.0 if d > 180.0 else d


# ------------------------------------------------------------- manual store

def _load_json(path: Path) -> dict:
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}
    return {}


def manual_get(camera_id: str) -> dict | None:
    return _load_json(MANUAL_FILE).get(camera_id)


def manual_set(camera_id: str, bearing_deg: float) -> dict:
    with _LOCK:
        d = _load_json(MANUAL_FILE)
        d[camera_id] = {"bearing_deg": round(bearing_deg % 360.0, 1),
                        "source": "manual",
                        "set_at": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
        MANUAL_FILE.write_text(json.dumps(d, indent=1), encoding="utf-8")
        return d[camera_id]


def manual_clear(camera_id: str) -> None:
    with _LOCK:
        d = _load_json(MANUAL_FILE)
        if camera_id in d:
            del d[camera_id]
            MANUAL_FILE.write_text(json.dumps(d, indent=1), encoding="utf-8")


# --------------------------------------------------------- satellite imagery

def _tile_xy(lat: float, lon: float, z: int) -> tuple[float, float]:
    n = 2 ** z
    x = (lon + 180.0) / 360.0 * n
    lat_r = math.radians(lat)
    y = (1.0 - math.asinh(math.tan(lat_r)) / math.pi) / 2.0 * n
    return x, y


def satellite_crop(cam: dict, zoom: int = 18, size: int = 512,
                   annotate: bool = True, client=None) -> bytes | None:
    """Satellite (Esri World Imagery) crop centered on the camera, with the
    two axis-hypothesis arrows drawn (A = way direction, B = opposite).
    Cached on disk — the satellite doesn't move."""
    cache = SAT_CACHE / f"{cam['camera_id']}_{zoom}_{int(annotate)}.png"
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
                r = client.get(ESRI_TILE.format(z=zoom, y=ty, x=tx), timeout=15)
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
            axis = cam.get("way_dir_deg", cam.get("road_axis_deg", 0.0)) or 0.0
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

        ok, buf = cv2.imencode(".png", crop)
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
    asymmetry vs the exact solar position. Coarse (+/-30 deg class), only
    fires when the evidence is unambiguous."""
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

    # Glare: heavily overexposed upper region => camera roughly faces the sun
    glare_frac = float((upper > 245).mean())
    if glare_frac > 0.10:
        pick, rel = (d0, rel0) if abs(rel0) < abs(rel1) else (d1, rel1)
        if abs(rel) < 75.0:
            conf = 0.5 if sun_el < 30.0 else 0.4
            return {"bearing_deg": pick, "conf": conf, "kind": "glare",
                    "sun_az": round(sun_az, 1), "sun_el": round(sun_el, 1),
                    "glare_frac": round(glare_frac, 3)}
        return None

    # Sky-brightness asymmetry: the upper-frame side toward the sun is
    # brighter when the sun is in the forward hemisphere.
    third = w // 3
    left = float(upper[:, :third].mean())
    right = float(upper[:, -third:].mean())
    grad = (right - left) / max(left, right, 1.0)
    if abs(grad) < 0.10:
        return None
    bright_side = 1.0 if grad > 0 else -1.0  # +1 right brighter
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
    """Overexposure-vs-time-of-day from the local snapshot cache: the frame
    with peak glare marks the hour the camera faced the sun. Needs history
    spanning several hours; strengthens automatically as the cache grows."""
    d = SNAP_CACHE / camera_id
    if not d.exists():
        return None
    samples = []
    for p in sorted(d.glob("*.jpg")):
        try:
            ts = float(p.stem)
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
    """SDOT camera keys sometimes end in the intersection corner the pole
    stands on (e.g. Terry_Thomas_NWC). The camera points across the
    intersection — away from its corner. Coarse (+/-45), snapped to axis."""
    key = (cam.get("key") or "").upper()
    for tok, b in CORNER_BEARING.items():
        if key.endswith("_" + tok):
            return b
    return None


def axis_token(cam: dict) -> str | None:
    """NS/EW suffix = which approach axis the camera watches. Cross-checks
    the OSM snap; a mismatch means the snap may have grabbed the cross street."""
    key = (cam.get("key") or "").upper()
    if key.endswith("_NS"):
        return "NS"
    if key.endswith("_EW"):
        return "EW"
    return None


def axis_token_consistent(cam: dict) -> bool | None:
    """None = no token; True/False = token agrees/disagrees with OSM axis."""
    tok = axis_token(cam)
    if tok is None or cam.get("road_axis_deg") is None:
        return None
    axis = cam["road_axis_deg"] % 180.0
    is_ns = axis < 45.0 or axis > 135.0
    return is_ns if tok == "NS" else not is_ns


# ------------------------------------------------------------------- fusion

def _satvlm_cached(camera_id: str) -> dict | None:
    return _load_json(SATVLM_FILE).get(camera_id)


def _satvlm_store(camera_id: str, result: dict | None) -> None:
    with _LOCK:
        d = _load_json(SATVLM_FILE)
        d[camera_id] = result or {"none": True, "at": time.time()}
        SATVLM_FILE.write_text(json.dumps(d, indent=1), encoding="utf-8")


def resolve(cam: dict, frame_bytes: bytes, fetched_at: float,
            flow: dict | None, client=None) -> dict:
    """Fuse all layers into (bearing_deg, resolved, dir_conf, basis, layers).
    Applies an axis-token sanity check on top of the layer result."""
    out = _resolve_layers(cam, frame_bytes, fetched_at, flow, client)
    if axis_token_consistent(cam) is False:
        out["layers"].append({
            "layer": "axis-token", "ok": False,
            "why": "key suffix (NS/EW) disagrees with OSM snap axis — "
                   "snap may have grabbed the cross street"})
        out["dir_conf"] = round(out["dir_conf"] * 0.8, 3)
        out["basis"] += "+axis-token-mismatch"
    return out


def _resolve_layers(cam: dict, frame_bytes: bytes, fetched_at: float,
                    flow: dict | None, client=None) -> dict:
    way_dir = cam.get("way_dir_deg")
    if way_dir is None:
        return {"bearing_deg": cam.get("road_axis_deg") or 0.0, "resolved": False,
                "dir_conf": 0.2, "basis": "no-map", "layers": []}
    d0, d1 = way_dir % 360.0, (way_dir + 180.0) % 360.0
    layers: list[dict] = []

    def snap(bearing: float) -> float | None:
        """Snap a layer's absolute bearing to the nearest axis hypothesis;
        reject when it contradicts the road axis outright."""
        cand = d0 if ang_diff(bearing, d0) <= ang_diff(bearing, d1) else d1
        return cand if ang_diff(bearing, cand) < 55.0 else None

    # L0 manual
    m = manual_get(cam["camera_id"])
    if m:
        b = snap(m["bearing_deg"])
        layers.append({"layer": "manual", "ok": b is not None, **m})
        if b is not None:
            return {"bearing_deg": b, "resolved": True, "dir_conf": 0.95,
                    "basis": "manual-confirmed", "layers": layers}

    # L1 oneway + flow (one-way streets only; flow says approach/recede)
    oneway = cam.get("oneway", "no")
    if oneway in ("yes", "true", "1", "-1") and flow and flow.get("n", 0) >= 6:
        travel = d0 if oneway != "-1" else d1
        picked = None
        if flow.get("away") is not None and flow["away"] > 0.65:
            picked, why = travel, "receding"
        elif flow.get("toward") is not None and flow["toward"] > 0.65:
            picked, why = (travel + 180.0) % 360.0, "approaching"
        if picked is not None:
            conf = min(0.9, 0.5 + max(flow.get("away") or 0, flow.get("toward") or 0) * 0.4)
            layers.append({"layer": "oneway+flow", "ok": True, "why": why,
                           "bearing_deg": picked, "conf": round(conf, 2)})
            return {"bearing_deg": picked, "resolved": True, "dir_conf": conf,
                    "basis": f"oneway+flow({why})", "layers": layers}
        layers.append({"layer": "oneway+flow", "ok": False, "why": "flow mixed"})

    # L2 satellite VLM cross-check (static per camera — cached persistently)
    if vlm.available():
        cached = _satvlm_cached(cam["camera_id"])
        if cached is None:
            sat = satellite_crop(cam, client=client)
            res = (vlm.satellite_cross_check(frame_bytes, sat, d0, d1)
                   if sat else None)
            _satvlm_store(cam["camera_id"], res)
            cached = res or {"none": True}
        if cached and not cached.get("none"):
            b = snap(cached["bearing_deg"])
            layers.append({"layer": "sat-vlm", "ok": b is not None, **cached})
            if b is not None:
                conf = {"high": 0.8, "medium": 0.7, "low": 0.55}.get(
                    cached.get("confidence", "low"), 0.55)
                return {"bearing_deg": b, "resolved": True, "dir_conf": conf,
                        "basis": f"satellite-vlm(arrow {cached['arrow']})",
                        "layers": layers}

        # L3 landmark VLM (per-frame; only when satellite was inconclusive)
        lm = vlm.landmark_bearing(frame_bytes, cam["lat"], cam["lon"])
        if lm:
            b = snap(lm["bearing_deg"])
            layers.append({"layer": "landmark-vlm", "ok": b is not None,
                           "bearing_deg": lm["bearing_deg"],
                           "n_landmarks": lm["n_landmarks"]})
            if b is not None:
                return {"bearing_deg": b, "resolved": True, "dir_conf": 0.75,
                        "basis": f"landmark-vlm({lm['n_landmarks']} landmarks)",
                        "layers": layers}

    # L3b corner token in the camera key (from safe-walk crawl: 39 cameras
    # carry NWC/NEC/SWC/SEC suffixes = the intersection corner the pole is
    # on; the camera points across the intersection, away from its corner)
    ct = corner_token_bearing(cam)
    if ct is not None:
        b = snap(ct)
        layers.append({"layer": "corner-token", "ok": b is not None,
                       "raw_bearing": ct})
        if b is not None:
            return {"bearing_deg": b, "resolved": True, "dir_conf": 0.5,
                    "basis": "corner-token(key suffix)", "layers": layers}

    # L4 sun history (overexposure peak across the local snapshot archive)
    sh = sun_history(cam["camera_id"], cam["lat"], cam["lon"], d0, d1)
    if sh:
        layers.append({"layer": "sun-history", "ok": True, **sh})
        return {"bearing_deg": sh["bearing_deg"], "resolved": True,
                "dir_conf": sh["conf"], "basis": "sun-history(overexposure peak)",
                "layers": layers}

    # L5 sun instant (glare / sky asymmetry on this frame)
    si = sun_instant(frame_bytes, fetched_at, cam["lat"], cam["lon"], d0, d1)
    if si:
        layers.append({"layer": "sun-instant", "ok": True, **si})
        return {"bearing_deg": si["bearing_deg"], "resolved": True,
                "dir_conf": si["conf"], "basis": f"sun-{si['kind']}",
                "layers": layers}

    layers.append({"layer": "fallback", "ok": False,
                   "why": "no layer resolved direction; pair-level hypothesis testing"})
    return {"bearing_deg": d0, "resolved": False, "dir_conf": 0.35,
            "basis": "axis-only", "layers": layers}
