"""M2: per-image geometry — vanishing point, horizon, slope, bearing.

Everything is recomputed from every fetched snapshot (PTZ cameras get
re-aimed); results are cached keyed by (camera_id, image_hash), never by
camera_id alone.

Bearing recovery:
  * The image gives the road axis in image space; the OSM snap gives the
    compass axis (two candidates, 180 deg apart).
  * On one-way streets with a live stream, optical flow (traffic
    approaching vs receding) + the mapped travel direction resolves the
    ambiguity outright.
  * Otherwise direction stays unresolved here; the pair classifier tests
    both hypotheses and reports which basis it used.
"""
from __future__ import annotations

import hashlib
import json
import math
import time
from dataclasses import asdict, dataclass
from pathlib import Path

import cv2
import numpy as np

from app import netboot

ROOT = Path(__file__).resolve().parent.parent
SNAP_CACHE = ROOT / "cache" / "snapshots"
GEOM_CACHE = ROOT / "cache" / "geometry"
SEG_CACHE = ROOT / "cache" / "segments"
for d in (SNAP_CACHE, GEOM_CACHE, SEG_CACHE):
    d.mkdir(parents=True, exist_ok=True)

SNAPSHOT_MIN_INTERVAL_S = 60.0  # fetch discipline: 1 request/camera/60s


@dataclass
class ViewGeometry:
    camera_id: str
    image_hash: str
    fetched_at: float
    vanishing_point: tuple[float, float] | None  # normalized 0-1
    road_axis_bearing_deg: float | None          # compass; may be axis-only
    direction_resolved: bool
    horizon_y: float | None
    slope_sign: int
    road_width_near: float | None
    confidence: float
    method: str
    n_lines: int
    flow: dict | None  # {"toward": f, "away": f, "pan": bool, "n": int}
    bearing_layers: list | None = None  # per-layer verdicts (diagnostics)


# ---------------------------------------------------------------- fetching

def _latest_cached(cam_id: str) -> tuple[Path, float] | None:
    d = SNAP_CACHE / cam_id
    if not d.exists():
        return None
    jpgs = sorted(d.glob("*.jpg"))
    if not jpgs:
        return None
    p = jpgs[-1]
    return p, float(p.stem)


def fetch_snapshot(cam: dict, client=None, force: bool = False) -> tuple[bytes, float, Path]:
    """Rate-limited snapshot fetch; returns (bytes, fetched_at, path)."""
    cam_id = cam["camera_id"]
    cached = _latest_cached(cam_id)
    now = time.time()
    if cached and not force and now - cached[1] < SNAPSHOT_MIN_INTERVAL_S:
        return cached[0].read_bytes(), cached[1], cached[0]
    close = False
    if client is None:
        client = netboot.make_client()
        close = True
    try:
        url = f"{cam['snapshot_url']}?{int(now * 1000)}"
        r = client.get(url, timeout=15)
        r.raise_for_status()
        data = r.content
        d = SNAP_CACHE / cam_id
        d.mkdir(exist_ok=True)
        path = d / f"{now:.0f}.jpg"
        path.write_bytes(data)
        return data, now, path
    except Exception:
        if cached:  # stale beats nothing
            return cached[0].read_bytes(), cached[1], cached[0]
        raise
    finally:
        if close:
            client.close()


# ------------------------------------------------------------ VP detection

def _detect_segments(gray: np.ndarray) -> np.ndarray:
    """Return line segments as (N,4) [x1,y1,x2,y2]."""
    try:
        lsd = cv2.createLineSegmentDetector()
        lines, *_ = lsd.detect(gray)
        if lines is not None and len(lines) > 8:
            return lines.reshape(-1, 4)
    except cv2.error:
        pass
    edges = cv2.Canny(gray, 60, 180)
    lines = cv2.HoughLinesP(edges, 1, np.pi / 180, threshold=40,
                            minLineLength=30, maxLineGap=8)
    if lines is None:
        return np.zeros((0, 4), dtype=np.float32)
    return lines.reshape(-1, 4).astype(np.float32)


def _filter_road_segments(segs: np.ndarray, w: int, h: int) -> np.ndarray:
    if len(segs) == 0:
        return segs
    dx = segs[:, 2] - segs[:, 0]
    dy = segs[:, 3] - segs[:, 1]
    length = np.hypot(dx, dy)
    ang = np.degrees(np.arctan2(np.abs(dy), np.abs(dx)))  # 0 = horizontal
    my = (segs[:, 1] + segs[:, 3]) / 2.0
    keep = (
        (length > 18)
        & (ang > 10) & (ang < 80)      # oblique: road edges / lane lines
        & (my > 0.35 * h)              # lower ~65 % of the frame
    )
    return segs[keep]


def _ransac_vp(segs: np.ndarray, w: int, h: int,
               rng: np.random.Generator | None = None) -> tuple[tuple[float, float] | None, int, list[int]]:
    """RANSAC over pairwise intersections; returns (vp_px, n_inliers, inlier_idx)."""
    n = len(segs)
    if n < 4:
        return None, 0, []
    rng = rng or np.random.default_rng(0)

    # Line params (a,b,c) for ax+by+c=0, normalized
    x1, y1, x2, y2 = segs[:, 0], segs[:, 1], segs[:, 2], segs[:, 3]
    a = y2 - y1
    b = x1 - x2
    c = -(a * x1 + b * y1)
    norm = np.hypot(a, b)
    a, b, c = a / norm, b / norm, c / norm
    length = np.hypot(x2 - x1, y2 - y1)

    n_pairs = min(3000, n * (n - 1) // 2)
    i_idx = rng.integers(0, n, n_pairs)
    j_idx = rng.integers(0, n, n_pairs)
    ok = i_idx != j_idx
    i_idx, j_idx = i_idx[ok], j_idx[ok]
    det = a[i_idx] * b[j_idx] - a[j_idx] * b[i_idx]
    good = np.abs(det) > 1e-6
    i_idx, j_idx, det = i_idx[good], j_idx[good], det[good]
    px = (b[i_idx] * c[j_idx] - b[j_idx] * c[i_idx]) / det
    py = (a[j_idx] * c[i_idx] - a[i_idx] * c[j_idx]) / det
    # Plausible VP region: roughly around/above the visual road, allow off-frame x
    keep = (px > -0.6 * w) & (px < 1.6 * w) & (py > -0.3 * h) & (py < 0.85 * h)
    px, py = px[keep], py[keep]
    if len(px) < 5:
        return None, 0, []

    cands = np.stack([px, py], axis=1)
    if len(cands) > 400:
        sel = rng.choice(len(cands), 400, replace=False)
        cands = cands[sel]

    # Inlier = segment whose infinite line passes within tol px of candidate
    tol = 0.02 * w
    best_score, best_vp, best_inl = -1.0, None, []
    A = np.stack([a, b, c], axis=1)
    for vx, vy in cands:
        dist = np.abs(A[:, 0] * vx + A[:, 1] * vy + A[:, 2])
        inl = dist < tol
        score = float((length * inl).sum())
        if score > best_score:
            best_score, best_vp, best_inl = score, (vx, vy), inl
    if best_vp is None:
        return None, 0, []

    # Refine: weighted least squares over inlier lines
    inl_idx = np.where(best_inl)[0]
    if len(inl_idx) >= 3:
        Ai = A[inl_idx]
        Wt = length[inl_idx]
        M = (Ai[:, :2] * Wt[:, None]).T @ Ai[:, :2]
        v = -(Ai[:, :2] * Wt[:, None]).T @ Ai[:, 2]
        try:
            sol = np.linalg.solve(M, v)
            if (-0.6 * w < sol[0] < 1.6 * w) and (-0.3 * h < sol[1] < 0.85 * h):
                best_vp = (float(sol[0]), float(sol[1]))
        except np.linalg.LinAlgError:
            pass
    return best_vp, int(best_inl.sum()), inl_idx.tolist()


def analyze_frame(img_bytes: bytes) -> dict:
    """Geometric pass on one frame. Returns image-space results (normalized)."""
    arr = np.frombuffer(img_bytes, dtype=np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if img is None:
        return {"ok": False, "reason": "decode failed"}
    scale = 640.0 / img.shape[1]
    img = cv2.resize(img, (640, int(img.shape[0] * scale)))
    h, w = img.shape[:2]
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    gray = cv2.createCLAHE(clipLimit=2.5, tileGridSize=(8, 8)).apply(gray)

    segs = _filter_road_segments(_detect_segments(gray), w, h)
    vp, n_inl, inl_idx = _ransac_vp(segs, w, h)

    if vp is None:
        return {"ok": True, "vp": None, "n_lines": int(len(segs)),
                "conf_geom": 0.0, "horizon_y": None, "slope_sign": 0,
                "road_width_near": None, "size": (w, h)}

    vpn = (vp[0] / w, vp[1] / h)
    conf = min(1.0, n_inl / 14.0) * (0.65 if len(segs) < 12 else 1.0)
    # Penalize VPs far outside the frame horizontally
    if vpn[0] < -0.1 or vpn[0] > 1.1:
        conf *= 0.6

    vy = vpn[1]
    slope_sign = 1 if vy < 0.40 else (-1 if vy > 0.55 else 0)

    # road width proxy: spread of inlier-line x-intercepts at the bottom row
    width = None
    if inl_idx:
        s = segs[inl_idx]
        x1, y1, x2, y2 = s[:, 0], s[:, 1], s[:, 2], s[:, 3]
        dy = y2 - y1
        okm = np.abs(dy) > 1e-3
        if okm.sum() >= 2:
            t = (h - y1[okm]) / dy[okm]
            xb = x1[okm] + t * (x2[okm] - x1[okm])
            xb = xb[(xb > -w) & (xb < 2 * w)]
            if len(xb) >= 2:
                width = float(np.clip((np.percentile(xb, 90) - np.percentile(xb, 10)) / w, 0, 2))

    return {"ok": True, "vp": vpn, "n_lines": int(len(segs)), "n_inliers": n_inl,
            "conf_geom": round(float(conf), 3), "horizon_y": round(float(vy), 3),
            "slope_sign": slope_sign,
            "road_width_near": None if width is None else round(width, 3),
            "size": (w, h)}


# ------------------------------------------------------------- optical flow

def fetch_ts_segment(cam: dict, client=None) -> Path | None:
    """Pull one live TS segment for a stream-backed camera; None otherwise."""
    if not cam.get("hls_url") or not cam.get("has_stream"):
        return None
    close = False
    if client is None:
        client = netboot.make_client()
        close = True
    try:
        base = cam["hls_url"].rsplit("/", 1)[0]
        master = client.get(cam["hls_url"], timeout=10).text
        chunklist = next((l for l in master.splitlines()
                          if l and not l.startswith("#")), None)
        if not chunklist:
            return None
        cl = client.get(f"{base}/{chunklist}", timeout=10).text
        seg_names = [l for l in cl.splitlines() if l and not l.startswith("#")]
        if not seg_names:
            return None
        seg = seg_names[-1]
        data = client.get(f"{base}/{seg}", timeout=15).content
        path = SEG_CACHE / f"{cam['camera_id']}_{int(time.time())}.ts"
        path.write_bytes(data)
        return path
    except Exception:
        return None
    finally:
        if close:
            client.close()


def _flow_vp(p0: np.ndarray, v: np.ndarray, w: int, h: int) -> tuple[tuple[float, float] | None, int]:
    """VP from flow: moving vehicles travel along the road, so the lines
    through each feature along its motion vector converge at the road's
    vanishing point. Far more robust than line detection on crosswalk-heavy
    or degraded frames."""
    mag = np.hypot(v[:, 0], v[:, 1])
    keep = mag > 1.5
    if keep.sum() < 8:
        return None, 0
    p, d, mg = p0[keep], v[keep], mag[keep]
    a = d[:, 1]
    b = -d[:, 0]
    norm = np.hypot(a, b)
    a, b = a / norm, b / norm
    c = -(a * p[:, 0] + b * p[:, 1])
    n = len(p)
    rng = np.random.default_rng(1)
    n_pairs = min(1500, n * (n - 1) // 2)
    i = rng.integers(0, n, n_pairs)
    j = rng.integers(0, n, n_pairs)
    ok = i != j
    i, j = i[ok], j[ok]
    det = a[i] * b[j] - a[j] * b[i]
    good = np.abs(det) > 1e-6
    i, j, det = i[good], j[good], det[good]
    px = (b[i] * c[j] - b[j] * c[i]) / det
    py = (a[j] * c[i] - a[i] * c[j]) / det
    # Tighter vertical band than the line-VP search: a road VP sits near or
    # above the vertical centre; low intersections are cross-street traffic.
    keep2 = (px > -0.6 * w) & (px < 1.6 * w) & (py > -0.3 * h) & (py < 0.62 * h)
    px, py = px[keep2], py[keep2]
    if len(px) < 5:
        return None, 0
    cands = np.stack([px, py], axis=1)
    if len(cands) > 300:
        cands = cands[rng.choice(len(cands), 300, replace=False)]
    tol = 0.03 * w
    best_score, best_vp, best_n = -1.0, None, 0
    for vx, vy in cands:
        dist = np.abs(a * vx + b * vy + c)
        inl = dist < tol
        score = float((mg * inl).sum())
        if score > best_score:
            best_score, best_vp, best_n = score, (float(vx), float(vy)), int(inl.sum())
    return best_vp, best_n


def flow_probe(ts_path: Path, vp_norm: tuple[float, float] | None) -> dict | None:
    """Sparse LK flow over one segment: flow-derived VP, traffic
    toward/away split, and pan detection."""
    cap = cv2.VideoCapture(str(ts_path))
    frames = []
    idx = 0
    while True:
        ok, f = cap.read()
        if not ok:
            break
        if idx % 6 == 0:  # ~5 fps sampling from ~30 fps
            frames.append(cv2.cvtColor(
                cv2.resize(f, (640, int(f.shape[0] * 640 / f.shape[1]))),
                cv2.COLOR_BGR2GRAY))
        idx += 1
        if len(frames) >= 8:
            break
    cap.release()
    if len(frames) < 2:
        return None

    h, w = frames[0].shape
    all_p0, all_v = [], []
    for i in range(len(frames) - 1):
        mask = np.zeros_like(frames[i])
        mask[int(0.30 * h):, :] = 255
        pts = cv2.goodFeaturesToTrack(frames[i], maxCorners=250,
                                      qualityLevel=0.01, minDistance=8,
                                      mask=mask)
        if pts is None:
            continue
        nxt, st, _err = cv2.calcOpticalFlowPyrLK(frames[i], frames[i + 1],
                                                 pts, None)
        if nxt is None:
            continue
        good = st.reshape(-1) == 1
        all_p0.append(pts.reshape(-1, 2)[good])
        all_v.append(nxt.reshape(-1, 2)[good] - pts.reshape(-1, 2)[good])

    if not all_p0:
        return None
    P0 = np.concatenate(all_p0)
    V = np.concatenate(all_v)
    mag = np.hypot(V[:, 0], V[:, 1])
    moving_frac = float((mag > 1.2).mean())

    # Pan signature: most features move, with a coherent direction
    pan = False
    if moving_frac > 0.65 and len(V) > 40:
        mv = V[mag > 1.2]
        mean = mv.mean(axis=0)
        coher = float(np.hypot(*mean) / max(np.hypot(mv[:, 0], mv[:, 1]).mean(), 1e-6))
        pan = coher > 0.8

    fvp, fvp_n = (None, 0) if pan else _flow_vp(P0, V, w, h)

    # Classify moving features toward/away relative to the best VP available
    ref_vp = fvp or (None if vp_norm is None else (vp_norm[0] * w, vp_norm[1] * h))
    toward = away = 0.0
    if ref_vp is not None:
        moving = mag > 1.2
        if moving.any():
            radial = P0[moving] - np.array(ref_vp)
            rn = radial / np.maximum(np.hypot(radial[:, 0], radial[:, 1]), 1e-6)[:, None]
            proj = (V[moving] * rn).sum(axis=1)
            toward = float((proj > 0.5).sum())   # diverging from VP = approaching camera
            away = float((proj < -0.5).sum())    # converging to VP = receding

    n = toward + away
    return {
        "toward": round(toward / n, 3) if n else None,
        "away": round(away / n, 3) if n else None,
        "n": int(n),
        "moving_frac": round(moving_frac, 3),
        "pan": bool(pan),
        "vp": None if fvp is None else (round(fvp[0] / w, 4), round(fvp[1] / h, 4)),
        "vp_n": fvp_n,
    }


# ------------------------------------------------------------ full pipeline

def compute_view_geometry(cam: dict, client=None, want_flow: bool = True) -> ViewGeometry:
    from app import bearing as bearing_mod  # deferred: avoids import cycle

    data, fetched_at, _path = fetch_snapshot(cam, client)
    img_hash = hashlib.sha1(data).hexdigest()[:16]

    # v2: cache key versioned — the bearing stack changed what's stored
    cache_file = GEOM_CACHE / f"v2_{cam['camera_id']}_{img_hash}.json"
    if cache_file.exists():
        d = json.loads(cache_file.read_text())
        return ViewGeometry(**d)

    frame = analyze_frame(data)
    flow = None
    if want_flow and cam.get("has_stream"):
        ts = fetch_ts_segment(cam, client)
        if ts is not None:
            flow = flow_probe(ts, frame.get("vp"))
            try:
                ts.unlink()
            except OSError:
                pass

    res = bearing_mod.resolve(cam, data, fetched_at, flow, client)
    bearing, resolved = res["bearing_deg"], res["resolved"]
    dir_conf, basis = res["dir_conf"], res["basis"]
    conf_geom = frame.get("conf_geom", 0.0)

    # VP fusion: flow-derived VP (vehicles move along the road) beats line
    # detection, which crosswalks and building edges routinely poison.
    vp = frame.get("vp")
    method = "lines"
    if flow and flow.get("vp") and flow.get("vp_n", 0) >= 10:
        fvp = tuple(flow["vp"])
        if vp is not None:
            agree = abs(fvp[0] - vp[0]) < 0.12 and abs(fvp[1] - vp[1]) < 0.12
            conf_geom = max(conf_geom, 0.75) if agree else min(0.6, 0.4 + flow["vp_n"] / 60.0)
            method = "flow+lines-agree" if agree else "flow(lines-disagree)"
        else:
            conf_geom = min(0.7, 0.35 + flow["vp_n"] / 60.0)
            method = "flow-only"
        vp = fvp
    elif vp is not None:
        conf_geom = min(conf_geom, 0.65)  # unverified line VP is never fully trusted

    horizon_y = None if vp is None else round(float(vp[1]), 3)
    slope_sign = 0
    if vp is not None:
        slope_sign = 1 if vp[1] < 0.40 else (-1 if vp[1] > 0.55 else 0)

    confidence = round(min(1.0, 0.55 * conf_geom + 0.45 * dir_conf +
                           (0.1 if resolved else 0.0)), 3)
    if flow and flow.get("pan"):
        confidence = min(confidence, 0.2)
        basis += "+PAN_DETECTED"

    vg = ViewGeometry(
        camera_id=cam["camera_id"],
        image_hash=img_hash,
        fetched_at=fetched_at,
        vanishing_point=vp,
        road_axis_bearing_deg=round(bearing, 1),
        direction_resolved=resolved,
        horizon_y=horizon_y,
        slope_sign=slope_sign,
        road_width_near=frame.get("road_width_near"),
        confidence=confidence,
        method=f"{method}/{basis}",
        n_lines=frame.get("n_lines", 0),
        flow=flow,
        bearing_layers=res.get("layers"),
    )
    cache_file.write_text(json.dumps(asdict(vg)))
    return vg
