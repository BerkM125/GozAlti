"""Local OpenCV CNN layer — vehicles + people, fully on this box (or the
DGX Spark), no external inference service.

Model: YOLOv4-tiny (AlexeyAB darknet, free/open, ~24 MB) through
cv2.dnn — download once via `python -m ingest.setup_cv`. Only COCO classes
person / bicycle / motorbike / car / bus / truck are kept.

Parallelism: a ProcessPoolExecutor (CV_WORKERS processes) — real processes,
so the Python GIL never serializes inference; each worker loads the network
once and keeps it warm. Frame fetching stays in the parent through
feeds.latest_frame(), so every upstream request still passes the module's
rate gates (>=60 s snapshots, >=10 s HLS, <=4 concurrent).

Result caching: inference results are cached per (camera, frame timestamp).
A caller polling faster than new frames arrive gets the cached result back
instantly with `cached: true` — zero extra upstream requests, zero wasted
CPU. This is what makes the UI's focus loop rate-limit-proof.

Pipelines:
  analyze_camera_cv(node)                  one camera end to end (3.1-style)
  analyze_point_cv(g, lat, lon, radius_m)  lat/lon -> cameras -> parallel
                                           frames -> parallel CNN -> math
"""
from __future__ import annotations

import threading
import time
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor

from . import config, feeds, locate

# COCO indices we keep (yolov4-tiny is trained on the 80-class COCO list)
KEEP_CLASSES = {0: "person", 1: "bicycle", 2: "car", 3: "motorbike",
                5: "bus", 7: "truck"}

_pool: ProcessPoolExecutor | None = None
_pool_lock = threading.Lock()
_cache: dict[str, dict] = {}          # camera_id -> last result
_cache_lock = threading.Lock()

# hot-camera prefetch: fetch+inference runs in the background as soon as a
# hot camera's frame gate opens, so API calls answer from cache in ms
_hot: dict[str, tuple[float, dict]] = {}     # camera_id -> (last_req_mono, node)
_hot_lock = threading.Lock()
_inflight: dict[str, threading.Event] = {}   # camera_id -> completion event
_inflight_lock = threading.Lock()
_prefetcher_started = False


def models_ready() -> bool:
    return all((config.MODELS_DIR / name).exists()
               for name in config.CV_MODEL_FILES)


def ensure_models(log=print) -> bool:
    """Download the CNN files if missing (called by ingest.setup_cv)."""
    from . import netboot
    ok = True
    client = netboot.make_client(timeout=180.0)
    try:
        for name, url in config.CV_MODEL_FILES.items():
            path = config.MODELS_DIR / name
            if path.exists():
                continue
            log(f"[setup_cv] downloading {name} ...")
            try:
                r = client.get(url)
                r.raise_for_status()
                path.write_bytes(r.content)
                log(f"[setup_cv] {name}: {len(r.content) / 1e6:.1f} MB")
            except Exception as exc:
                log(f"[setup_cv] FAILED {name}: {exc}")
                ok = False
    finally:
        client.close()
    return ok and models_ready()


# ---------------------------------------------------------------- workers

def _detect_in_worker(jpeg: bytes) -> dict:
    """Runs inside a worker process. Loads the net once per process
    (module-level global), then serves forward passes."""
    import cv2                     # local import: keep parent spawn cheap
    import numpy as np

    global _NET, _OUT_NAMES        # per-process globals
    try:
        _NET
    except NameError:
        _NET = cv2.dnn.readNetFromDarknet(
            str(config.MODELS_DIR / "yolov4-tiny.cfg"),
            str(config.MODELS_DIR / "yolov4-tiny.weights"))
        _NET.setPreferableBackend(cv2.dnn.DNN_BACKEND_OPENCV)
        _OUT_NAMES = _NET.getUnconnectedOutLayersNames()

    img = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        return {"error": "decode failed"}
    h, w = img.shape[:2]
    size = config.CV_INPUT_SIZE
    blob = cv2.dnn.blobFromImage(img, 1 / 255.0, (size, size),
                                 swapRB=True, crop=False)
    _NET.setInput(blob)
    outputs = _NET.forward(_OUT_NAMES)

    boxes, confs, classes = [], [], []
    for out in outputs:
        for row in out:
            scores = row[5:]
            cls = int(np.argmax(scores))
            conf = float(scores[cls] * row[4])
            if cls not in KEEP_CLASSES or conf < config.CV_CONF_THRESHOLD:
                continue
            bcx, bcy, bw, bh = row[0], row[1], row[2], row[3]
            boxes.append([int((bcx - bw / 2) * w), int((bcy - bh / 2) * h),
                          int(bw * w), int(bh * h)])
            confs.append(conf)
            classes.append(cls)

    if not boxes:
        return {"w": w, "h": h, "detections": []}
    keep = cv2.dnn.NMSBoxes(boxes, confs, config.CV_CONF_THRESHOLD,
                            config.CV_NMS_THRESHOLD)
    detections = []
    for i in (keep.flatten() if len(keep) else []):
        x, y, bw, bh = boxes[i]
        detections.append({
            "label": KEEP_CLASSES[classes[i]],
            "conf": round(confs[i], 3),
            "box": [max(0.0, x / w), max(0.0, y / h),
                    min(1.0, (x + bw) / w), min(1.0, (y + bh) / h)],
        })
    return {"w": w, "h": h, "detections": detections}


def _get_pool() -> ProcessPoolExecutor:
    global _pool
    with _pool_lock:
        if _pool is None:
            _pool = ProcessPoolExecutor(max_workers=config.CV_WORKERS)
        return _pool


# --------------------------------------------------------------- pipelines

def _iso_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def analyze_camera_cv(node: dict, force: bool = False) -> dict:
    """Full single-camera pipeline: rate-gated frame -> CNN in a worker
    process -> mathematics layer -> cached result. Marks the camera hot so
    the prefetcher keeps its cache warm; if an analysis for this camera is
    already in flight, waits for that instead of duplicating work."""
    cid = node["camera_id"]
    if not models_ready():
        return {"camera_id": cid, "ok": False,
                "why": "CNN not installed — run: python -m ingest.setup_cv"}
    with _hot_lock:
        _hot[cid] = (time.monotonic(), node)
    _ensure_prefetcher()

    with _inflight_lock:
        ev = _inflight.get(cid)
    if ev is not None:
        ev.wait(timeout=10)
        with _cache_lock:
            cached = _cache.get(cid)
        if cached:
            return {**cached, "cached": True}
    return _analyze_inline(node, force)


def _analyze_inline(node: dict, force: bool = False) -> dict:
    cid = node["camera_id"]
    with _inflight_lock:
        if cid in _inflight:           # lost the race — serve current cache
            with _cache_lock:
                cached = _cache.get(cid)
            if cached:
                return {**cached, "cached": True}
        ev = _inflight.setdefault(cid, threading.Event())
    try:
        return _analyze_locked(node, force)
    finally:
        with _inflight_lock:
            _inflight.pop(cid, None)
        ev.set()


def _analyze_locked(node: dict, force: bool) -> dict:
    cid = node["camera_id"]
    t0 = time.monotonic()
    blob, rec = feeds.latest_frame(node, prefer="hls")
    if blob is None or rec is None:
        return {"camera_id": cid, "ok": False,
                "why": "no frame (rate-gated with empty cache, dead, or offline)"}

    with _cache_lock:
        cached = _cache.get(cid)
    if cached and not force and cached.get("frame_ts") == rec["captured_at"]:
        return {**cached, "cached": True}

    try:
        raw = _get_pool().submit(_detect_in_worker, blob).result(timeout=60)
    except Exception as exc:
        return {"camera_id": cid, "ok": False, "why": f"cv worker: {exc}"}
    if "error" in raw:
        return {"camera_id": cid, "ok": False, "why": raw["error"]}

    placed = locate.place_all(node, raw["detections"], raw["w"], raw["h"])
    result = {
        "camera_id": cid,
        "ok": True,
        "analyzed_at": _iso_now(),
        "frame_ts": rec["captured_at"],
        "frame": rec,
        "model": "yolov4-tiny(opencv-dnn,local)",
        "took_ms": round((time.monotonic() - t0) * 1000),
        "detections": placed,
        "cached": False,
    }
    with _cache_lock:
        _cache[cid] = result
    # co-presence from the local lane too — evidence-attached
    if any(d["label"] == "person" for d in placed):
        node["copresence"] = {
            "last_person_at": result["analyzed_at"],
            "seen_by": cid,
            "source": "cv-local",
            "frame": rec.get("path"),
        }
    return result


def _ensure_prefetcher() -> None:
    global _prefetcher_started
    if not config.CV_PREFETCH or _prefetcher_started:
        return
    with _hot_lock:
        if _prefetcher_started:
            return
        _prefetcher_started = True
    threading.Thread(target=_prefetch_loop, daemon=True,
                     name="cv-prefetch").start()


def _prefetch_loop() -> None:
    """Keep hot cameras' results warm: the moment a hot camera's frame gate
    opens, fetch + infer in the background. Requests then always hit cache.
    Upstream cadence is still bounded by the same gates — this changes WHEN
    the work happens, never HOW OFTEN."""
    while True:
        time.sleep(0.3)
        now = time.monotonic()
        with _hot_lock:
            expired = [c for c, (ts, _) in _hot.items()
                       if now - ts > config.CV_HOT_TTL_S]
            for c in expired:
                _hot.pop(c, None)
            items = [(c, node) for c, (ts, node) in _hot.items()]
        for cid, node in items:
            key, interval = feeds.frame_gate_key(node)
            if feeds.gate_remaining(key, interval) > 0:
                continue        # no new frame possible yet — stay idle
            with _inflight_lock:
                if cid in _inflight:
                    continue
            threading.Thread(target=_analyze_inline, args=(node,),
                             daemon=True).start()


def analyze_point_cv(g, lat: float, lon: float,
                     radius_m: float = 150.0) -> dict:
    """lat/lon -> cameras -> frames in parallel (rate gates hold) -> CNN
    forward passes in parallel worker processes -> mathematics layer."""
    t0 = time.monotonic()
    # nearby() returns copies — resolve back to the live graph nodes so
    # co-presence updates land on the artifact
    nodes = [g.nodes[c["camera_id"]]
             for c in g.nearby(lat, lon, radius_m)[:config.CV_MAX_POINT_CAMERAS]]
    if not nodes:
        return {"query": {"lat": lat, "lon": lon, "radius_m": radius_m},
                "cameras": [], "took_ms": 0}
    with ThreadPoolExecutor(max_workers=len(nodes)) as tp:
        results = list(tp.map(analyze_camera_cv, nodes))
    return {
        "query": {"lat": lat, "lon": lon, "radius_m": radius_m},
        "cameras": results,
        "n_detections": sum(len(r.get("detections", [])) for r in results),
        "took_ms": round((time.monotonic() - t0) * 1000),
    }


def status() -> dict:
    with _hot_lock:
        hot = sorted(_hot.keys())
    return {
        "models_ready": models_ready(),
        "model": "yolov4-tiny (opencv-dnn, fully local)",
        "workers": config.CV_WORKERS,
        "classes": sorted(KEEP_CLASSES.values()),
        "input_size": config.CV_INPUT_SIZE,
        "prefetch": config.CV_PREFETCH,
        "hot_cameras": hot,
        "install": "python -m ingest.setup_cv" if not models_ready() else None,
    }
