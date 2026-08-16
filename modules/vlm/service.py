#!/usr/bin/env python3
"""vlm module service — SPEC §6.8, port 8040.

  POST /read     FrameRecord (§6.1) -> Observation (§6.2). Also accepts media-ingest's
                 hot-lane envelope {frame_record, image_b64, prior_observations}.
  POST /read_batch  {"frames":[FrameRecord,...]} -> {"observations":[Observation,...]}
  GET  /health   liveness + what is loaded
  GET  /flags    the closed flag enum this module emits
  GET  /cache    cache stats;  DELETE /cache clears it
  GET  /         a zero-dependency demo page that exercises all of the above
  GET  /frames   sample frames available to the demo
  GET  /frame?path=...  serve a frame (restricted to under GOZALTI_ROOT)

Runs INSIDE the vLLM container so it has torch/torchvision/cv2, with --network host so
it can reach ollama on :11434:

  docker run -d --name vlm-svc --gpus all --network host \
    -v /home/acer01/GozAlti:/repo -w /repo/modules/vlm \
    -v /home/acer01/junk/torchcache:/root/.cache/torch \
    --entrypoint python3 vllm/vllm-openai:latest service.py

Stdlib only, deliberately: no venv to build on a shared box, nothing to install when the
venue wifi dies. The cost is hand-rolled validation, which is done strictly below.

Two things this enforces that the scripts did not:
  - the response is validated against §6.2 before it leaves; on a schema miss the VLM is
    retried once, then the observation degrades to detector-only rather than shipping
    malformed JSON downstream (SPEC §7.4: fail loudly, never bend the shape).
  - a frame marked stale by media-ingest is never sent to a model. It returns
    camera_dead with no detections and no invented caption.
"""
import json, os, re, sys, tempfile, time, threading, base64, urllib.request
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "lab"))

PORT = int(os.environ.get("VLM_PORT", "8040"))
OLLAMA = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434")
VLM_MODEL = os.environ.get("VLM_MODEL", "qwen3-vl:8b")
DET_ARCH = os.environ.get("VLM_DET_ARCH", "fasterrcnn")
REPO = Path(os.environ.get("GOZALTI_ROOT", "/repo"))

# Measured on the GB10, not guessed (6 frames, qwen3-vl:8b):
#   concurrency 1 -> 3.22 s/frame   2 -> 2.25 s/frame   4 -> 2.27 s/frame
# ollama saturates at 2 with default settings; beyond that throughput is flat and
# per-call latency doubles (6.95 s), which only hurts anyone waiting on a single read.
# Raising it needs OLLAMA_NUM_PARALLEL on the ollama service, which needs root.
# The detector is NOT batched: measured 74.5 ms/frame at batch 1 and 85-87 ms batched,
# because frames are different sizes and get padded to the largest. It stays serial
# under _lock; VLM calls overlap around it, which pipelines the two stages for free.
CONCURRENCY = int(os.environ.get("VLM_CONCURRENCY", "2"))
MAX_BATCH = int(os.environ.get("VLM_MAX_BATCH", "64"))

# Result cache. SPEC puts "storing history beyond a small result cache" out of scope,
# so the small result cache is in. It is what makes multiple users affordable: a route
# sweep touches ~40 cameras, and two people walking overlapping routes would otherwise
# each pay 2.3 s of GPU for the identical frame.
#
# The key is the FRAME, not the camera: (camera_id, path, mtime). SDOT refreshes on a
# ~15 min sweep, so a camera's answer is valid exactly as long as its frame is unchanged.
# Keying on the frame rather than a wall-clock TTL means we can never serve a stale
# reading of a frame that has already been replaced, and never re-read one that has not.
# TTL is a backstop for the case where a camera stops updating.
CACHE_TTL = int(os.environ.get("VLM_CACHE_TTL", "900"))     # seconds; 0 disables
CACHE_MAX = int(os.environ.get("VLM_CACHE_MAX", "2000"))    # entries, LRU

# The closed flag enum this module emits (SPEC §6.2 says it is defined here).
FLAGS = [
    "no_people",            # detector found nobody
    "crowd",                # unusually many people for this sweep
    "blocked_sidewalk",     # walking path impassable
    "narrowed_sidewalk",    # walking path reduced but passable
    "no_sidewalk",          # no built pedestrian infrastructure here
    "construction",         # construction activity visible
    "road_closure",         # street closed / diverted
    "emergency_response",   # emergency vehicles or flashing lights
    "queue",                # people queueing
    "loading",              # loading/unloading in progress
    "stalled_vehicle",      # vehicle stopped where it should not be
    "transit_stop",         # bus/tram stop in frame, in use
    "vehicle_on_sidewalk",  # vehicle encroaching on the walking path
    "camera_dead",          # placeholder/maintenance frame, nothing read
    "poor_lighting",        # dark and unlit
]
FLAGSET = set(FLAGS)

_det = None
_lock = threading.Lock()
_stats = {"reads": 0, "schema_retries": 0, "schema_failures": 0, "dead": 0,
          "batches": 0, "batch_frames": 0, "cache_hits": 0, "cache_misses": 0,
          "gpu_seconds_saved": 0.0, "started": time.time()}
_cache = OrderedDict()          # key -> (stored_at, observation)
_cache_lock = threading.Lock()
_pool = ThreadPoolExecutor(max_workers=max(1, CONCURRENCY), thread_name_prefix="vlm")


def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def resolve(path):
    """FrameRecord paths are repo-relative or absolute."""
    p = Path(path)
    return p if p.is_absolute() else (REPO / p)


def unwrap(payload):
    """Accept either a bare FrameRecord (§6.1) or media-ingest's hot-lane envelope.

    media-ingest pushes {"frame_record": <§6.1 untouched>, "image_b64": ...,
    "prior_observations": [last <=3 Observations]}. `prior_observations` is a sibling
    key, deliberately NOT a §6.1 field, so the contract stays unedited — confirmed as
    tolerated here (media-ingest/SPEC.md asked the vlm owner to confirm).

    Sending the bytes inline is the better shape and we prefer it: media-ingest owns
    rate limiting and fetching, and this service runs containerised where its host paths
    may not resolve. If image_b64 is present we never touch the filesystem.
    """
    if isinstance(payload.get("frame_record"), dict):
        rec = dict(payload["frame_record"])
        return rec, payload.get("image_b64"), payload.get("prior_observations") or []
    return payload, payload.get("image_b64"), payload.get("prior_observations") or []


def materialise(rec, image_b64):
    """Bytes on the wire beat a path we might not be able to see. Returns (path, tmp?)."""
    if image_b64:
        raw = base64.b64decode(image_b64)
        tmp = Path(tempfile.gettempdir()) / f"vlm_{abs(hash(image_b64[:512]))}.jpg"
        tmp.write_bytes(raw)
        return tmp, True
    path = resolve(rec.get("path", ""))
    if not path.exists():
        raise FileNotFoundError(f"frame not found: {path} (send image_b64 to avoid "
                                f"depending on paths this service can resolve)")
    return path, False


# ---------- cache ------------------------------------------------------------------

def cache_key(rec, path):
    """Identity of the *frame*: the bytes on disk, not the metadata describing them.

    Deliberately excludes captured_at. Two FrameRecords pointing at the same file with
    the same mtime describe the same pixels no matter what timestamp the caller
    attached, and reading those pixels twice cannot produce a different answer. Keying
    on captured_at made every re-request a miss whenever a caller stamped it with now(),
    which is exactly what the demo page did — 2.5 s of GPU for a frame we had already
    read. camera_id stays in the key so two cameras that somehow share a path never
    alias.
    """
    try:
        st = path.stat()
        ident = (int(st.st_mtime), st.st_size)
    except OSError:
        ident = (0, 0)
    return (rec.get("camera_id"), str(path), *ident)


def cache_get(key):
    if not CACHE_TTL:
        return None
    with _cache_lock:
        hit = _cache.get(key)
        if not hit:
            return None
        stored_at, obs = hit
        if time.time() - stored_at > CACHE_TTL:
            _cache.pop(key, None)
            return None
        _cache.move_to_end(key)                 # LRU
        return obs


def cache_put(key, obs):
    if not CACHE_TTL:
        return
    with _cache_lock:
        _cache[key] = (time.time(), obs)
        _cache.move_to_end(key)
        while len(_cache) > CACHE_MAX:
            _cache.popitem(last=False)


# ---------- illumination -----------------------------------------------------------

# CPTED's seven factors put lighting among the strongest positive contributors to how
# safe a street feels (see ../SAFETY-SIGNALS.md). We measure it rather than ask the VLM:
# safe-walk found the VLM calling a 2 a.m. street "daylight", and a histogram cannot
# hallucinate. ~1 ms on top of a 66 ms detector pass.
#
# CALIBRATION, MEASURED: across 35 frames spanning day and 21:47-local night, mean luma
# ran 82.6 to 150.2 and EVERY frame bucketed "lit". SDOT cameras auto-expose, so a dark
# street does not produce a dark image — the sensor compensates with gain. Absolute luma
# is therefore a weak proxy for "can a walker see here", and these thresholds are
# provisional: they have never yet separated a real frame into dark or dim.
#
# What is trustworthy is the raw triple, which ships in _ext regardless of the bucket:
#   mean_luma      overall exposure after the camera's own gain
#   dark_fraction  share of frame below luma 30 — survives auto-exposure better, because
#                  gain cannot recover detail from a genuinely black region
#   spread         separates an evenly lit street from one streetlight against black
#
# The durable fix is the same one used for population and traffic: rank a camera against
# the rest of the sweep rather than against an absolute number (see detlib.rank and
# ../SAFETY-SIGNALS.md §3). That needs a sweep, so it belongs in synthesis, not here.
# Until then poor_lighting fires rarely by design — a flag that never fires is better
# than one tuned until it does.
LUMA_DARK = 45.0        # provisional, unvalidated on this fleet
LUMA_DIM = 80.0         # provisional, unvalidated on this fleet


def illumination(img_path):
    """Mean luma, the fraction of the frame that is near-black, and a coarse bucket."""
    try:
        import cv2, numpy as np
        im = cv2.imread(str(img_path))
        if im is None:
            return None
        y = cv2.cvtColor(im, cv2.COLOR_BGR2GRAY)
        mean = float(y.mean())
        # how much of the frame a walker simply cannot see into
        dark_frac = float((y < 30).sum()) / y.size
        # spread separates "evenly lit" from "one bright streetlight, rest black"
        spread = float(y.std())
        bucket = ("dark" if mean < LUMA_DARK else
                  "dim" if mean < LUMA_DIM else "lit")
        return {"mean_luma": round(mean, 1), "dark_fraction": round(dark_frac, 3),
                "spread": round(spread, 1), "bucket": bucket}
    except Exception:
        return None


# ---------- detector ---------------------------------------------------------------

def detector():
    global _det
    if _det is None:
        import detlib
        _det = detlib.load(DET_ARCH, 0.5, "cuda", None)
    return _det


def run_detector(img_path):
    import detlib
    det = detector()
    tensor, W, H = detlib.read_tensor(det, str(img_path))
    out, ms = detlib.infer(det, tensor)
    people, vehicles = detlib.parse(det, out, W, H, 0.5, 0.5)
    return people, vehicles, round(ms, 1)


# ---------- VLM --------------------------------------------------------------------

INSIGHT_PROMPT = (HERE / "lab" / "prompts" / "insight.txt").read_text()

WALKWAY_FLAG = {"blocked": "blocked_sidewalk", "narrowed": "narrowed_sidewalk",
                "no_sidewalk": "no_sidewalk"}
EVENT_FLAG = {e: e for e in ("construction", "road_closure", "emergency_response",
                             "queue", "loading", "stalled_vehicle", "transit_stop")}


def ask_vlm(img_path, context, max_tokens=400):
    body = {"model": VLM_MODEL, "prompt": context + INSIGHT_PROMPT,
            "images": [base64.b64encode(Path(img_path).read_bytes()).decode()],
            "stream": False, "format": "json", "keep_alive": "24h", "think": False,
            "options": {"temperature": 0, "num_predict": max_tokens, "num_ctx": 16384}}
    req = urllib.request.Request(f"{OLLAMA}/api/generate", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=300) as r:
        o = json.load(r)
    return o.get("response") or o.get("thinking") or ""


def parse_json(text):
    try:
        return json.loads(text)
    except Exception:
        m = re.search(r"\{.*\}", text, re.S)
        if not m:
            return None
        try:
            return json.loads(re.sub(r",\s*([}\]])", r"\1", m.group(0)))
        except Exception:
            return None


def as_list(v):
    """Models disagree on whether a one-item enum list is a list; Cosmos returns a bare
    string, which silently iterates as characters if you trust the schema."""
    if isinstance(v, str):
        return [x.strip() for x in v.split(",") if x.strip()]
    if isinstance(v, list):
        return [x for x in v if isinstance(x, str)]
    return []


def priors_block(priors):
    """What this camera showed recently, so the VLM can notice CHANGE rather than
    re-describe a static scene. Capped and phrased as history, never as current fact."""
    if not priors:
        return ""
    lines = []
    for p in list(priors)[-3:]:
        if not isinstance(p, dict):
            continue
        when = p.get("frame_ts") or p.get("read_at") or "earlier"
        cap = (p.get("caption") or "").strip()[:140]
        fl = ", ".join(p.get("flags") or []) or "no flags"
        lines.append(f"- {when}: {fl}. {cap}")
    if not lines:
        return ""
    return ("Earlier reads of THIS SAME camera, oldest first. Use them only to notice what "
            "has changed; do not repeat them as if they were the current frame:\n"
            + "\n".join(lines) + "\n\n")


def context_block(people, vehicles):
    v = {k: n for k, n in vehicles.items() if n}
    return (f"A detector has already counted this frame: {len(people)} people outside "
            f"vehicles, {sum(vehicles.values())} vehicles"
            + (f" ({', '.join(f'{n} {k}' for k, n in v.items())})" if v else "")
            + ".\n\n")


# ---------- Observation ------------------------------------------------------------

def validate(obs):
    """Strict check against §6.2. Returns list of problems; empty means valid."""
    bad = []
    for k in ("camera_id", "frame_ts", "read_at", "model", "people_count",
              "detections", "flags", "caption"):
        if k not in obs:
            bad.append(f"missing {k}")
    if not isinstance(obs.get("people_count"), int):
        bad.append("people_count not int")
    if not isinstance(obs.get("caption"), str):
        bad.append("caption not str")
    for f in obs.get("flags") or []:
        if f not in FLAGSET:
            bad.append(f"flag not in enum: {f}")
    for d in obs.get("detections") or []:
        if not isinstance(d, dict):
            bad.append("detection not object"); continue
        for k in ("label", "cx", "cy", "conf"):
            if k not in d:
                bad.append(f"detection missing {k}")
        for k in ("cx", "cy"):
            v = d.get(k)
            if not isinstance(v, (int, float)) or not (0.0 <= v <= 1.0):
                bad.append(f"detection {k} not in [0,1]: {v}")
    return bad


def observe(rec, image_b64=None, priors=None):
    """FrameRecord -> Observation. Optionally with inline bytes and temporal breadcrumbs."""
    cam = rec.get("camera_id") or "unknown"
    frame_ts = rec.get("captured_at") or now_iso()
    base = {"camera_id": cam, "frame_ts": frame_ts, "read_at": now_iso(),
            "model": f"torchvision/{DET_ARCH}+{VLM_MODEL}", "people_count": 0,
            "detections": [], "flags": [], "caption": ""}

    # a dead camera is never sent to a model, and never gets an invented caption
    if rec.get("stale") is True:
        _stats["dead"] += 1
        base["flags"] = ["camera_dead"]
        base["caption"] = "camera returned a maintenance placeholder; nothing observed"
        base["model"] = "none"
        return base

    path, is_tmp = materialise(rec, image_b64)

    key = cache_key(rec, path)
    cached = cache_get(key)
    if cached is not None:
        _stats["cache_hits"] += 1
        _stats["gpu_seconds_saved"] = round(
            _stats["gpu_seconds_saved"] + (cached.get("_ext", {}).get("total_s") or 2.3), 2)
        out = dict(cached)
        out["read_at"] = now_iso()               # when WE answered
        out["_ext"] = dict(cached.get("_ext", {}), cached=True)
        return out
    _stats["cache_misses"] += 1
    t_start = time.time()

    lum = illumination(path)          # ~1 ms, no GPU, cannot hallucinate

    with _lock:                       # one GPU, one detector at a time
        people, vehicles, det_ms = run_detector(path)

    base["people_count"] = len(people)
    base["detections"] = [{"label": p["label"], "cx": p["cx"], "cy": p["cy"],
                           "conf": p["conf"]} for p in people]
    flags = []
    if not people:
        flags.append("no_people")
    # poor_lighting comes from the measurement, not from the model's opinion. It fires
    # when the frame is genuinely dark OR when most of it is unreadable even though a
    # single light source pulls the mean up.
    if lum and (lum["bucket"] == "dark" or lum["dark_fraction"] > 0.55):
        flags.append("poor_lighting")

    ctx = context_block(people, vehicles) + priors_block(priors)
    ins, tries = None, 0
    for attempt in (1, 2):
        try:
            ins = parse_json(ask_vlm(path, ctx))
        except Exception:
            ins = None
        tries = attempt
        if ins is not None:
            break
        _stats["schema_retries"] += 1

    if ins is None:
        # degrade to detector-only rather than ship malformed JSON downstream
        _stats["schema_failures"] += 1
        base["flags"] = flags
        base["caption"] = (f"{len(people)} people and {sum(vehicles.values())} vehicles "
                           f"detected; scene description unavailable")
        base["model"] = f"torchvision/{DET_ARCH} (vlm failed)"
        base["_degraded"] = True
    else:
        for e in as_list(ins.get("events")):
            if e in EVENT_FLAG and EVENT_FLAG[e] not in flags:
                flags.append(EVENT_FLAG[e])
        w = WALKWAY_FLAG.get(ins.get("walkway_status"))
        if w:
            flags.append(w)
        base["flags"] = [f for f in flags if f in FLAGSET]
        parts = [ins.get("activity") or "", ins.get("walkway_reason") or "",
                 ins.get("setting_notes") or ""]
        base["caption"] = " ".join(p.strip() for p in parts if p.strip())[:400]

    # extensions beyond §6.2 — additive, consumers may ignore
    base["_ext"] = {"illumination": lum,
                    "vehicles": vehicles, "vehicle_count": sum(vehicles.values()),
                    "detector_ms": det_ms, "vlm_tries": tries,
                    "scene": (ins or {}).get("scene"),
                    "walkway_status": (ins or {}).get("walkway_status"),
                    "vlm_confidence": (ins or {}).get("confidence"),
                    "total_s": round(time.time() - t_start, 2), "cached": False}
    cache_put(key, base)
    return base


def read_one(payload):
    """One FrameRecord (bare or enveloped) -> Observation, or {camera_id, error}. Never
    raises: a batch of 40 must not die because one camera's frame went missing."""
    rec, img_b64, priors = unwrap(payload if isinstance(payload, dict) else {})
    try:
        obs = observe(rec, img_b64, priors)
        problems = validate(obs)
        if problems:
            return {"camera_id": rec.get("camera_id"), "error": "schema",
                    "problems": problems[:4]}
        _stats["reads"] += 1
        return obs
    except FileNotFoundError as e:
        return {"camera_id": rec.get("camera_id"), "error": str(e)}
    except Exception as e:
        return {"camera_id": rec.get("camera_id"), "error": f"{type(e).__name__}: {e}"}


# ---------- HTTP -------------------------------------------------------------------

class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def _json(self, code, payload):
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        p = urlparse(self.path).path
        if p == "/health":
            return self._json(200, {
                "module": "vlm", "port": PORT, "ok": True,
                "detector": DET_ARCH, "detector_loaded": _det is not None,
                "vlm": VLM_MODEL, "ollama": OLLAMA,
                "concurrency": CONCURRENCY, "max_batch": MAX_BATCH,
                "cache_ttl_s": CACHE_TTL, "cache_entries": len(_cache),
                "uptime_s": round(time.time() - _stats["started"]),
                **{k: v for k, v in _stats.items() if k != "started"}})
        if p in ("/", "/demo", "/index.html"):
            html = (HERE / "demo.html").read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(html)))
            self.end_headers()
            self.wfile.write(html)
            return
        if p == "/frames":
            # what the demo can read: the committed sample set
            out = []
            for f in sorted((HERE / "lab" / "samples").glob("*.jpg")):
                parts = f.stem.split("__")
                out.append({"name": f.name, "path": str(f),
                            "camera_id": parts[1] if len(parts) >= 3 else f.stem})
            return self._json(200, {"frames": out})
        if p == "/frame":
            q = parse_qs(urlparse(self.path).query).get("path", [""])[0]
            target = Path(q).resolve()
            # never serve outside the repo, however the caller spells the path
            if REPO.resolve() not in target.parents or not target.is_file():
                return self._json(404, {"error": "not found"})
            data = target.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "image/jpeg")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)
            return
        if p == "/flags":
            return self._json(200, {"flags": FLAGS})
        if p == "/cache":
            h, m = _stats["cache_hits"], _stats["cache_misses"]
            return self._json(200, {
                "entries": len(_cache), "max": CACHE_MAX, "ttl_s": CACHE_TTL,
                "hits": h, "misses": m,
                "hit_rate": round(h / (h + m), 3) if (h + m) else None,
                "gpu_seconds_saved": _stats["gpu_seconds_saved"]})
        self._json(404, {"error": "not found", "routes": ["/health", "/flags",
                                                          "POST /read", "POST /read_batch"]})

    def do_DELETE(self):
        if urlparse(self.path).path == "/cache":
            with _cache_lock:
                n = len(_cache); _cache.clear()
            return self._json(200, {"cleared": n})
        self._json(404, {"error": "not found"})

    def do_POST(self):
        p = urlparse(self.path).path
        try:
            n = int(self.headers.get("Content-Length", 0))
            payload = json.loads(self.rfile.read(n)) if n else {}
        except Exception as e:
            return self._json(400, {"error": f"bad json: {e}"})

        try:
            if p == "/read":
                rec, img_b64, priors = unwrap(payload)
                obs = observe(rec, img_b64, priors)
                problems = validate(obs)
                if problems:
                    return self._json(500, {"error": "observation failed schema",
                                            "problems": problems, "observation": obs})
                _stats["reads"] += 1
                return self._json(200, obs)
            if p == "/read_batch":
                frames = payload.get("frames", [])
                if not isinstance(frames, list):
                    return self._json(400, {"error": "frames must be a list"})
                if len(frames) > MAX_BATCH:
                    return self._json(413, {"error": f"batch too large: {len(frames)} > "
                                                     f"{MAX_BATCH}", "max_batch": MAX_BATCH})
                t0 = time.time()
                # Order is preserved and one bad frame never sinks the batch: each item
                # returns either an Observation or {camera_id, error}.
                out = list(_pool.map(read_one, frames))
                wall = time.time() - t0
                ok = sum(1 for o in out if "error" not in o)
                _stats["batches"] += 1
                _stats["batch_frames"] += len(frames)
                return self._json(200, {
                    "observations": out,
                    "count": len(out), "ok": ok, "failed": len(out) - ok,
                    "wall_s": round(wall, 2),
                    "per_frame_s": round(wall / len(out), 2) if out else None,
                    "concurrency": CONCURRENCY})
        except FileNotFoundError as e:
            return self._json(404, {"error": str(e)})
        except Exception as e:
            return self._json(500, {"error": f"{type(e).__name__}: {e}"})
        self._json(404, {"error": "not found"})


def main():
    print(f"vlm service :{PORT}  detector={DET_ARCH}  vlm={VLM_MODEL}  repo={REPO}  "
          f"concurrency={CONCURRENCY}  max_batch={MAX_BATCH}  "
          f"cache_ttl={CACHE_TTL}s", flush=True)
    if os.environ.get("VLM_PRELOAD", "1") == "1":
        threading.Thread(target=lambda: (detector(), print("[vlm] detector warm", flush=True)),
                         daemon=True).start()
    ThreadingHTTPServer(("0.0.0.0", PORT), H).serve_forever()


if __name__ == "__main__":
    main()
