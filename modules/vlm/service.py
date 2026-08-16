#!/usr/bin/env python3
"""vlm module service — SPEC §6.8, port 8040.

  POST /read     FrameRecord (§6.1)  -> Observation (§6.2)
  POST /read_batch  {"frames":[FrameRecord,...]} -> {"observations":[Observation,...]}
  GET  /health   liveness + what is loaded
  GET  /flags    the closed flag enum this module emits

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
import json, os, re, sys, time, threading, base64, urllib.request
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "lab"))

PORT = int(os.environ.get("VLM_PORT", "8040"))
OLLAMA = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434")
VLM_MODEL = os.environ.get("VLM_MODEL", "qwen3-vl:8b")
DET_ARCH = os.environ.get("VLM_DET_ARCH", "fasterrcnn")
REPO = Path(os.environ.get("GOZALTI_ROOT", "/repo"))

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
_stats = {"reads": 0, "schema_retries": 0, "schema_failures": 0, "dead": 0, "started": time.time()}


def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def resolve(path):
    """FrameRecord paths are repo-relative or absolute."""
    p = Path(path)
    return p if p.is_absolute() else (REPO / p)


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


def observe(rec):
    """FrameRecord -> Observation."""
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

    path = resolve(rec.get("path", ""))
    if not path.exists():
        raise FileNotFoundError(f"frame not found: {path}")

    with _lock:                       # one GPU, one detector at a time
        people, vehicles, det_ms = run_detector(path)

    base["people_count"] = len(people)
    base["detections"] = [{"label": p["label"], "cx": p["cx"], "cy": p["cy"],
                           "conf": p["conf"]} for p in people]
    flags = []
    if not people:
        flags.append("no_people")

    ctx = context_block(people, vehicles)
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
        if ins.get("lighting") == "dark_unlit":
            flags.append("poor_lighting")
        base["flags"] = [f for f in flags if f in FLAGSET]
        parts = [ins.get("activity") or "", ins.get("walkway_reason") or "",
                 ins.get("setting_notes") or ""]
        base["caption"] = " ".join(p.strip() for p in parts if p.strip())[:400]

    # extensions beyond §6.2 — additive, consumers may ignore
    base["_ext"] = {"vehicles": vehicles, "vehicle_count": sum(vehicles.values()),
                    "detector_ms": det_ms, "vlm_tries": tries,
                    "scene": (ins or {}).get("scene"),
                    "walkway_status": (ins or {}).get("walkway_status"),
                    "vlm_confidence": (ins or {}).get("confidence")}
    return base


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
                "uptime_s": round(time.time() - _stats["started"]),
                **{k: v for k, v in _stats.items() if k != "started"}})
        if p == "/flags":
            return self._json(200, {"flags": FLAGS})
        self._json(404, {"error": "not found", "routes": ["/health", "/flags",
                                                          "POST /read", "POST /read_batch"]})

    def do_POST(self):
        p = urlparse(self.path).path
        try:
            n = int(self.headers.get("Content-Length", 0))
            payload = json.loads(self.rfile.read(n)) if n else {}
        except Exception as e:
            return self._json(400, {"error": f"bad json: {e}"})

        try:
            if p == "/read":
                obs = observe(payload)
                problems = validate(obs)
                if problems:
                    return self._json(500, {"error": "observation failed schema",
                                            "problems": problems, "observation": obs})
                _stats["reads"] += 1
                return self._json(200, obs)
            if p == "/read_batch":
                out = []
                for rec in payload.get("frames", []):
                    try:
                        obs = observe(rec)
                        if validate(obs):
                            obs = {"camera_id": rec.get("camera_id"), "error": "schema"}
                        else:
                            _stats["reads"] += 1
                    except Exception as e:
                        obs = {"camera_id": rec.get("camera_id"), "error": str(e)}
                    out.append(obs)
                return self._json(200, {"observations": out})
        except FileNotFoundError as e:
            return self._json(404, {"error": str(e)})
        except Exception as e:
            return self._json(500, {"error": f"{type(e).__name__}: {e}"})
        self._json(404, {"error": "not found"})


def main():
    print(f"vlm service :{PORT}  detector={DET_ARCH}  vlm={VLM_MODEL}  repo={REPO}", flush=True)
    if os.environ.get("VLM_PRELOAD", "1") == "1":
        threading.Thread(target=lambda: (detector(), print("[vlm] detector warm", flush=True)),
                         daemon=True).start()
    ThreadingHTTPServer(("0.0.0.0", PORT), H).serve_forever()


if __name__ == "__main__":
    main()
