"""Per-segment LLM summaries — served by Ollama ON the DGX Spark.

The deterministic layer computes risk; OpenCV counts what the cameras see.
This component phrases that evidence as one-line human summaries for the
segment popup. It NEVER adds facts: the model receives ONLY the segment's
evidence payload and is instructed to restate it; the reply is labeled as
LLM phrasing of deterministic evidence, not a safety verdict. Backend
unreachable -> honest {"available": false, why}, never canned text
pretending to be model output.

Backend: the Spark's Ollama (binds 127.0.0.1:11434 on the box; media-ingest
runs on the Spark in the demo so the default just works). Local dev off-box
needs a forward, e.g.  ssh -N -L 11435:127.0.0.1:11434 spark  and
OLLAMA_URL=http://127.0.0.1:11435. Model default: qwen2.5:3b-instruct
(pulled on the Spark; non-thinking, ~1 s/summary warm — keeps up with live
revisions). OLLAMA_MODEL overrides; the team's warm qwen3-vl:8b works too
but thinks first (~7 s/summary). keep_alive rides at 24h so our calls
never shorten any model's residency.

Queue semantics ("queued as data arrives, modified as the live feed
updates"): enqueue(path_id, segments) coalesces per (path_id, seg_key) on
evidence_rev (hash of the payload). New evidence for a pending key replaces
its payload in place; new evidence for a finished key re-queues it and the
old text keeps serving with revising=true until the new one lands. ONE
worker thread -> at most one in-flight generation on the shared Spark GPU.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
from collections import OrderedDict

import httpx

OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/")
# Default: the fastest NON-thinking model already pulled on the Spark —
# measured ~1 s/summary warm, so 50 segments keep up with live revisions.
# The team's warm qwen3-vl:8b also works (OLLAMA_MODEL=qwen3-vl:8b) but its
# template always emits chain-of-thought first: ~7 s/summary measured.
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "qwen2.5:3b-instruct")
KEEP_ALIVE = "24h"            # never shorten a model's residency on the box
GEN_TIMEOUT_S = 90.0
MAX_PATHS = 8                 # results kept for the last N path_ids

SYSTEM_PROMPT = (
    "You caption street-segment safety evidence for a PEDESTRIAN routing "
    "demo — people WALKING, never driving. Reply with ONE plain-text "
    "sentence under 28 words. Use ONLY facts present in the JSON: name the "
    "two or three factors with the LARGEST values and say they drive the "
    "rating. Never mention factors whose value is 0 or absent; never invent "
    "numbers, events, hazards, or reassurance. No preamble, no quotes, no "
    "markdown.")

_lock = threading.Lock()
_queue: "OrderedDict[tuple[str, str], dict]" = OrderedDict()   # pending work
_results: "OrderedDict[str, dict]" = OrderedDict()             # path_id -> {seg_key: {...}}
_worker: threading.Thread | None = None
_wake = threading.Event()

_avail: dict = {"ok": None, "why": "not checked yet", "at": 0.0}


def _rev(payload: dict) -> str:
    return hashlib.sha1(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:12]


def _check_available(force: bool = False) -> bool:
    """Cached reachability probe of the Ollama endpoint (60 s)."""
    now = time.monotonic()
    if not force and _avail["ok"] is not None and now - _avail["at"] < 60:
        return _avail["ok"]
    try:
        r = httpx.get(f"{OLLAMA_URL}/api/version", timeout=4.0)
        r.raise_for_status()
        _avail.update(ok=True, why="", at=now)
    except Exception as exc:
        _avail.update(ok=False, at=now,
                      why=f"ollama unreachable at {OLLAMA_URL}: "
                          f"{type(exc).__name__} (on the Spark it is "
                          "localhost:11434; off-box set OLLAMA_URL to an "
                          "ssh -L forward)")
    return _avail["ok"]


def _strip_think(text: str) -> str:
    return re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip()


def _generate(payload: dict) -> str:
    """One chat call. Raises on failure — caller records the miss honestly."""
    body = {
        "model": OLLAMA_MODEL,
        "messages": [{"role": "system", "content": SYSTEM_PROMPT},
                     {"role": "user", "content": json.dumps(payload)}],
        "stream": False,
        "keep_alive": KEEP_ALIVE,
        # 400 tokens: thinking-template models (qwen3*) burn ~250 on
        # chain-of-thought BEFORE the answer lands in content — a tight cap
        # yields done_reason=length with EMPTY content (measured). Ollama
        # keeps thinking out of message.content, so the extra budget only
        # costs latency on those models, nothing on non-thinking ones.
        "options": {"temperature": 0.2, "num_predict": 400},
        "think": False,
    }
    try:
        r = httpx.post(f"{OLLAMA_URL}/api/chat", json=body, timeout=GEN_TIMEOUT_S)
        r.raise_for_status()
    except httpx.HTTPStatusError:
        body.pop("think", None)            # older ollama: no think param
        r = httpx.post(f"{OLLAMA_URL}/api/chat", json=body, timeout=GEN_TIMEOUT_S)
        r.raise_for_status()
    text = _strip_think(r.json().get("message", {}).get("content", ""))
    if not text:
        raise RuntimeError("empty completion")
    return text


def _work() -> None:
    while True:
        with _lock:
            key = next(iter(_queue), None)
            item = _queue.pop(key) if key else None
        if item is None:
            _wake.wait(timeout=5.0)
            _wake.clear()
            continue
        path_id, seg_key = key
        if not _check_available():
            continue                       # queue drains; get() reports why
        try:
            text = _generate(item["payload"])
            err = None
        except Exception as exc:
            text, err = None, f"{type(exc).__name__}: {exc}"
            _check_available(force=True)
        with _lock:
            store = _results.setdefault(path_id, {})
            prev = store.get(seg_key, {})
            if text is not None:
                store[seg_key] = {
                    "text": text, "model": OLLAMA_MODEL,
                    "evidence_rev": item["rev"],
                    "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    "revising": False,
                    "basis": "llm-phrased deterministic+opencv evidence",
                }
            else:                          # keep old text; surface the miss
                store[seg_key] = {**prev, "revising": False,
                                  "error": err} if prev else {
                    "text": None, "model": OLLAMA_MODEL, "error": err,
                    "evidence_rev": item["rev"], "revising": False}
            while len(_results) > MAX_PATHS:
                _results.popitem(last=False)


def _ensure_worker() -> None:
    global _worker
    if _worker is None or not _worker.is_alive():
        _worker = threading.Thread(target=_work, daemon=True,
                                   name="pathfind-summarize")
        _worker.start()


def enqueue(path_id: str, segments: list[dict]) -> dict:
    """Queue/refresh summaries. Each segment: {seg_key, ...evidence fields}.
    Coalesces on evidence_rev — unchanged evidence is never re-generated."""
    _ensure_worker()
    queued = skipped = 0
    with _lock:
        for seg in segments:
            seg_key = str(seg.get("seg_key") or seg.get("segment_id") or "")
            if not seg_key:
                continue
            payload = {k: v for k, v in seg.items() if k != "seg_key"}
            rev = _rev(payload)
            done = _results.get(path_id, {}).get(seg_key)
            if done and done.get("evidence_rev") == rev and done.get("text"):
                skipped += 1
                continue                   # same evidence, same summary
            if done and done.get("text"):
                done["revising"] = True    # old text serves until replaced
            _queue[(path_id, seg_key)] = {"payload": payload, "rev": rev}
            queued += 1
    _wake.set()
    return {"queued": queued, "unchanged": skipped,
            "available": _check_available(), "model": OLLAMA_MODEL}


def get(path_id: str) -> dict:
    with _lock:
        summaries = dict(_results.get(path_id, {}))
        pending = sum(1 for (p, _s) in _queue if p == path_id)
    ok = _check_available()
    return {"available": ok, "why": None if ok else _avail["why"],
            "model": OLLAMA_MODEL, "endpoint": OLLAMA_URL,
            "pending": pending, "summaries": summaries}


def status() -> dict:
    ok = _check_available()
    with _lock:
        depth = len(_queue)
        paths = list(_results)
    return {"available": ok, "why": None if ok else _avail["why"],
            "model": OLLAMA_MODEL, "endpoint": OLLAMA_URL,
            "queue_depth": depth, "paths": paths}


def enqueue_from_path(path: dict) -> dict:
    """Hook for live.py: queue every segment of a (re)computed PathObject.
    Evidence = the segment's own risk_parts + live camera reports."""
    reporting = (path.get("live") or {}).get("cameras_reporting", {})
    segs = []
    for s in path.get("segments", []):
        cams = s.get("cameras", [])
        segs.append({
            "seg_key": s["segment_id"],
            "name": s.get("name"),
            "rating": s.get("risk_bucket"),
            "risk_score": s.get("risk"),
            "length_m": s.get("length_m"),
            "night": path.get("night"),
            "factors": s.get("risk_parts"),
            "cameras_covering": cams,
            "live_camera_reports": {c: reporting[c] for c in cams
                                    if c in reporting},
        })
    return enqueue(path["path_id"], segs)
