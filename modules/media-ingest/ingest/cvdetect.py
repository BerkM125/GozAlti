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

from . import config, feeds, locate, stream

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


# ------------------------------------------- backend: Adi's detlib (truth)
# Inference is reconciled with modules/vlm/lab/detlib.py — Adi's torchvision
# detector stack is the SOURCE OF TRUTH for what counts as a detection
# (weights, transforms, thresholds, class set). We import his module
# read-only and adapt its raw output; the yolo path below is only a
# latency fallback for CPU-only boxes and is labeled as such in results.

_dl: dict | None = None
_dl_lock = threading.Lock()
_dl_error: str | None = None

# detlib uses COCO names; map onto the label set locate.py/UI already speak
_LABEL_MAP = {"motorcycle": "motorbike"}


def _resolve_backend() -> str:
    if config.CV_BACKEND in ("yolo", "detlib"):
        return config.CV_BACKEND
    try:                       # auto: source of truth wherever it's fast
        import torch
        return "detlib" if torch.cuda.is_available() else "yolo"
    except ImportError:
        return "yolo"


def _load_detlib() -> dict:
    import sys as _sys
    lab = str(config.REPO_ROOT / "modules" / "vlm" / "lab")
    if lab not in _sys.path:
        _sys.path.insert(0, lab)
    import detlib as dl
    import torch
    device = "cuda" if torch.cuda.is_available() else "cpu"
    det = dl.load(config.CV_ARCH, thresh=config.CV_CONF_THRESHOLD,
                  device=device, min_size=config.CV_MIN_SIZE)
    return {"dl": dl, "det": det, "torch": torch, "device": device}


def _detect_detlib(frame) -> dict:
    """One forward pass through Adi's detector. `frame` is JPEG bytes or a
    BGR ndarray (from the live streamer). Serialized by a lock — a single
    warm model instance, exactly like his lab scripts run it."""
    global _dl, _dl_error
    import cv2
    import numpy as np
    with _dl_lock:
        if _dl is None:
            try:
                _dl = _load_detlib()
            except Exception as exc:
                _dl_error = str(exc)
                return {"error": f"detlib backend unavailable: {exc}"}
        dl, det, torch = _dl["dl"], _dl["det"], _dl["torch"]
        img = (cv2.imdecode(np.frombuffer(frame, np.uint8), cv2.IMREAD_COLOR)
               if isinstance(frame, (bytes, bytearray)) else frame)
        if img is None:
            return {"error": "decode failed"}
        h, w = img.shape[:2]
        rgb = np.ascontiguousarray(img[:, :, ::-1])
        tensor = det.tf(torch.from_numpy(rgb).permute(2, 0, 1)).to(det.device)
        out, infer_ms = dl.infer(det, tensor)

    detections = []
    for box, label, sc in zip(out["boxes"].tolist(), out["labels"].tolist(),
                              out["scores"].tolist()):
        name = det.names[label] if label < len(det.names) else str(label)
        if sc < config.CV_CONF_THRESHOLD:
            continue
        if name != "person" and name not in dl.VEHICLE_CLASSES:
            continue
        x1, y1, x2, y2 = box
        detections.append({
            "label": _LABEL_MAP.get(name, name),
            "conf": round(sc, 3),
            "box": [max(0.0, x1 / w), max(0.0, y1 / h),
                    min(1.0, x2 / w), min(1.0, y2 / h)],
        })
    return {"w": w, "h": h, "detections": detections,
            "infer_ms": round(infer_ms)}


# ------------------------------------------ backend: yolo fallback workers

def _prep_fast(frame):
    """Laptop fast path: shrink + compress a frame BEFORE it crosses the
    process boundary. Streamed frames arrive as full-res raw BGR ndarrays
    (~6 MB to pickle per inference); bounding the long side to
    CV_PREP_MAX_DIM and re-encoding as JPEG cuts that to tens of KB, and
    the worker's decode/blob step gets cheaper too. Uniform scale keeps the
    aspect ratio, so locate.py's ratio-based bearing/range math is
    untouched. yolo-path only — detlib keeps the full frame."""
    import cv2
    import numpy as np

    max_dim = config.CV_PREP_MAX_DIM
    if max_dim <= 0:
        return frame
    if isinstance(frame, (bytes, bytearray)):
        if len(frame) <= config.CV_PREP_MAX_JPEG_KB * 1024:
            return frame          # already compact — skip a decode/re-encode
        img = cv2.imdecode(np.frombuffer(frame, np.uint8), cv2.IMREAD_COLOR)
        if img is None:
            return frame          # let the worker report the decode failure
    else:
        img = frame
    h, w = img.shape[:2]
    scale = max_dim / max(w, h)
    if scale < 1.0:
        img = cv2.resize(img, (max(1, round(w * scale)), max(1, round(h * scale))),
                         interpolation=cv2.INTER_AREA)
    ok, buf = cv2.imencode(".jpg", img,
                           [cv2.IMWRITE_JPEG_QUALITY, config.CV_PREP_JPEG_Q])
    return buf.tobytes() if ok else frame


def _detect_in_worker(frame) -> dict:
    """Runs inside a worker process. Loads the net once per process
    (module-level global), then serves forward passes. `frame` is JPEG
    bytes or a BGR ndarray."""
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

    img = (cv2.imdecode(np.frombuffer(frame, np.uint8), cv2.IMREAD_COLOR)
           if isinstance(frame, (bytes, bytearray)) else frame)
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


def _iso_at(wall_ts: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(wall_ts))


def backend_ready(backend: str | None = None) -> tuple[bool, str]:
    """(ready, why-not). detlib needs torch importable; yolo needs the
    downloaded model files."""
    if (backend or _resolve_backend()) == "detlib":
        try:
            import torch  # noqa: F401
            return True, ""
        except ImportError:
            return False, "detlib backend: torch not installed — " \
                          "python -m ingest.setup_cv installs it"
    if not models_ready():
        return False, "CNN not installed — run: python -m ingest.setup_cv"
    return True, ""


def analyze_camera_cv(node: dict, force: bool = False,
                      backend: str | None = None) -> dict:
    """Full single-camera pipeline: freshest frame (live streamer when the
    camera has one, rate-gated fetch otherwise) -> CNN -> mathematics layer
    -> cached result. Marks the camera hot so the prefetcher + streamer keep
    its cache warm; concurrent calls dedupe onto one in-flight analysis.
    `backend` forces "detlib" (HQ pass, Adi's stack) or "yolo" per request."""
    cid = node["camera_id"]
    ready, why = backend_ready(backend)
    if not ready:
        return {"camera_id": cid, "ok": False, "why": why}
    with _hot_lock:
        _hot[cid] = (time.monotonic(), node)
    _ensure_prefetcher()

    if backend is None:                # forced-backend calls skip the dedup
        with _inflight_lock:
            ev = _inflight.get(cid)
        if ev is not None:
            ev.wait(timeout=10)
            with _cache_lock:
                cached = _cache.get(cid)
            if cached:
                return {**cached, "cached": True}
    return _analyze_inline(node, force, backend)


def _analyze_inline(node: dict, force: bool = False,
                    backend: str | None = None) -> dict:
    cid = node["camera_id"]
    with _inflight_lock:
        if cid in _inflight and backend is None:   # lost the race — use cache
            with _cache_lock:
                cached = _cache.get(cid)
            if cached:
                return {**cached, "cached": True}
        ev = _inflight.setdefault(cid, threading.Event())
    try:
        return _analyze_locked(node, force, backend)
    finally:
        with _inflight_lock:
            _inflight.pop(cid, None)
        ev.set()


def _frame_for(node: dict):
    """(frame_input, rec) — live streamer frame when available (lowest
    latency), else the rate-gated fetch path. frame_input is a BGR ndarray
    (stream) or JPEG bytes (fetch)."""
    cid = node["camera_id"]
    if node.get("has_stream"):
        # never evict from here — the UI's focused-camera endpoint is the
        # only caller allowed to displace another streamer
        stream.ensure(node, evict=False)
        f, wall = stream.latest(cid)
        if f is not None:
            rec = {"camera_id": cid,
                   "captured_at": _iso_at(wall),
                   "lat": node["lat"], "lon": node["lon"],
                   "kind": "frame", "path": None,
                   "source": "sdot-hls", "stale": False, "live_stream": True}
            return f, rec
    blob, rec = feeds.latest_frame(node, prefer="hls")
    return blob, rec


def _analyze_locked(node: dict, force: bool,
                    backend_override: str | None = None) -> dict:
    cid = node["camera_id"]
    t0 = time.monotonic()
    frame_input, rec = _frame_for(node)
    if frame_input is None or rec is None:
        return {"camera_id": cid, "ok": False,
                "why": "no frame (rate-gated with empty cache, dead, or offline)"}

    with _cache_lock:
        cached = _cache.get(cid)
    if cached and not force and cached.get("frame_ts") == rec["captured_at"]:
        return {**cached, "cached": True}

    backend = backend_override or _resolve_backend()
    if backend == "detlib":
        raw = _detect_detlib(frame_input)
        model_name = (f"detlib/{config.CV_ARCH}@{(_dl or {}).get('device', '?')} "
                      "(vlm-lab, source of truth)")
    else:
        try:
            raw = _get_pool().submit(_detect_in_worker,
                                     _prep_fast(frame_input)).result(timeout=60)
        except Exception as exc:
            return {"camera_id": cid, "ok": False, "why": f"cv worker: {exc}"}
        model_name = "yolov4-tiny(opencv-dnn, cpu-latency fallback)"
    if "error" in raw:
        return {"camera_id": cid, "ok": False, "why": raw["error"]}

    placed = locate.place_all(node, raw["detections"], raw["w"], raw["h"])
    result = {
        "camera_id": cid,
        "ok": True,
        "analyzed_at": _iso_now(),
        "frame_ts": rec["captured_at"],
        "frame": rec,
        "model": model_name,
        "backend": backend,
        "infer_ms": raw.get("infer_ms"),
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
            streamed = False
            if node.get("has_stream"):
                # live streamer when a slot is free (never churn-evict from
                # the prefetcher); full house falls back to the gated path
                s = stream.ensure(node, evict=False)
                if s is not None:
                    f, wall = s.latest()
                    if f is None:
                        continue        # streamer still spinning up
                    with _cache_lock:
                        cached = _cache.get(cid)
                    if cached and cached.get("frame_ts") == _iso_at(wall):
                        continue
                    streamed = True
            if not streamed:
                key, interval = feeds.frame_gate_key(node)
                if feeds.gate_remaining(key, interval) > 0:
                    continue    # no new frame possible yet — stay idle
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


def mark_hot(node: dict) -> None:
    """Public hook for other components (pathfind live sessions): put a
    camera on the prefetcher's hot list WITHOUT running anything inline.
    The prefetcher then keeps its CV cache warm at the gated cadence."""
    with _hot_lock:
        _hot[node["camera_id"]] = (time.monotonic(), node)
    _ensure_prefetcher()


def cached_result(cid: str) -> dict | None:
    """Read-only view of the last CV result for a camera. Never blocks."""
    with _cache_lock:
        return _cache.get(cid)


def status() -> dict:
    with _hot_lock:
        hot = sorted(_hot.keys())
    backend = _resolve_backend()
    ready, why = backend_ready()
    return {
        "backend": backend,
        "source_of_truth": "modules/vlm/lab/detlib.py (Adi) — yolo is a "
                           "cpu-latency fallback only",
        "model": (f"detlib/{config.CV_ARCH} (torchvision, vlm-lab)"
                  if backend == "detlib"
                  else "yolov4-tiny (opencv-dnn, cpu-latency fallback)"),
        "device": (_dl or {}).get("device"),
        "ready": ready,
        "why_not_ready": why or None,
        "workers": config.CV_WORKERS if backend == "yolo" else 1,
        "classes": sorted(KEEP_CLASSES.values()),
        "prefetch": config.CV_PREFETCH,
        "prep": ({"max_dim": config.CV_PREP_MAX_DIM,
                  "jpeg_q": config.CV_PREP_JPEG_Q}
                 if config.CV_PREP_MAX_DIM > 0 else None),
        "hot_cameras": hot,
        "streamers": stream.active(),
        "detlib_error": _dl_error,
    }
