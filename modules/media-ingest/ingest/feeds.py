"""Frame acquisition with rate discipline — the module's core responsibility.

Two paths to a current frame:
  * HLS (357 SDOT cameras): fetch playlist -> chunklist -> newest TS segment
    via httpx (goes through the DNS-over-TCP proxy, unlike ffmpeg URLs),
    decode the segment's last frame with OpenCV. More up-to-date than
    snapshots, and segment pulls don't hold streams open.
  * Snapshot (everything): rate-limited to >=60 s per camera, dead-camera
    placeholder detection ported from safe-walk (hash blocklist + magic
    bytes; a hash seen on >=3 distinct URLs in one sweep is boilerplate).

Every capture emits a FrameRecord (SPEC §6.1) — appended to
data/frame_records.jsonl and kept in memory for `latest_record()`.

Upstream concurrency is capped globally at FETCH_CONCURRENCY (=4).
"""
from __future__ import annotations

import hashlib
import json
import re
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin

import cv2
import numpy as np

from . import config, netboot

_FETCH_SEM = threading.BoundedSemaphore(config.FETCH_CONCURRENCY)
_LAST_FETCH: dict[str, float] = {}       # camera_id -> monotonic ts of last upstream hit
_LAST_FETCH_LOCK = threading.Lock()
_LATEST: dict[str, dict] = {}            # camera_id -> last FrameRecord
_RECORDS_LOCK = threading.Lock()
_PLACEHOLDER_LOCK = threading.Lock()

_client = None
_client_lock = threading.Lock()


def client():
    global _client
    with _client_lock:
        if _client is None:
            _client = netboot.make_client(timeout=config.FETCH_TIMEOUT)
        return _client


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _stamp(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y%m%dT%H%M%SZ")


# ----------------------------------------------------------- placeholder db

def _load_placeholder_hashes() -> set[str]:
    if config.PLACEHOLDER_HASHES.exists():
        try:
            return set(json.loads(config.PLACEHOLDER_HASHES.read_text()))
        except json.JSONDecodeError:
            return set()
    return set()


def learn_placeholder_hashes(md5_by_url: dict[str, str]) -> set[str]:
    """Sweep-level learning: a hash appearing on >=3 distinct image URLs in
    one pass cannot be a real street view."""
    url_count: dict[str, set[str]] = {}
    for url, h in md5_by_url.items():
        if h:
            url_count.setdefault(h, set()).add(url)
    learned = {h for h, urls in url_count.items() if len(urls) >= 3}
    with _PLACEHOLDER_LOCK:
        known = _load_placeholder_hashes() | learned
        config.PLACEHOLDER_HASHES.write_text(json.dumps(sorted(known), indent=2))
    return known


def is_placeholder(blob: bytes) -> bool:
    return hashlib.md5(blob).hexdigest() in _load_placeholder_hashes()


# ------------------------------------------------------------ rate limiting

def _gate(camera_id: str, min_interval: float) -> bool:
    """True if an upstream fetch is allowed now; records the attempt."""
    now = time.monotonic()
    with _LAST_FETCH_LOCK:
        last = _LAST_FETCH.get(camera_id)
        if last is not None and now - last < min_interval:
            return False
        _LAST_FETCH[camera_id] = now
        return True


def _cached_latest_path(camera_id: str) -> Path | None:
    d = config.FRAMES / camera_id
    if not d.exists():
        return None
    jpgs = sorted(d.glob("*.jpg"))
    return jpgs[-1] if jpgs else None


# ----------------------------------------------------------------- records

def _emit(camera_id: str, node: dict, ts: float, path: Path | None,
          source: str, stale: bool, kind: str = "frame") -> dict:
    rec = {
        "camera_id": camera_id,
        "captured_at": _iso(ts),
        "lat": node["lat"], "lon": node["lon"],
        "kind": kind,
        "path": (str(path.relative_to(config.MODULE_ROOT)).replace("\\", "/")
                 if path else None),
        "source": source,
        "stale": stale,
    }
    with _RECORDS_LOCK:
        _LATEST[camera_id] = rec
        with config.FRAME_RECORDS_JSONL.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec) + "\n")
    return rec


def latest_record(camera_id: str) -> dict | None:
    rec = _LATEST.get(camera_id)
    if rec:
        return rec
    p = _cached_latest_path(camera_id)
    return None if p is None else _LATEST.setdefault(camera_id, {
        "camera_id": camera_id,
        "captured_at": _iso(p.stat().st_mtime),
        "lat": None, "lon": None, "kind": "frame",
        "path": str(p.relative_to(config.MODULE_ROOT)).replace("\\", "/"),
        "source": "disk-cache", "stale": False,
    })


def _store(camera_id: str, blob: bytes, ts: float) -> Path:
    d = config.FRAMES / camera_id
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{_stamp(ts)}.jpg"
    p.write_bytes(blob)
    _prune_camera(d)
    return p


def _prune_camera(d: Path) -> None:
    frames = sorted(d.glob("*.jpg"))
    for old in frames[:-config.KEEP_RECENT_PER_CAM]:
        old.unlink(missing_ok=True)


# ---------------------------------------------------------------- snapshot

def snapshot_frame(node: dict, force: bool = False) -> tuple[bytes | None, dict | None]:
    """Rate-limited snapshot fetch. Returns (jpeg, FrameRecord) — falls back
    to the newest cached frame when inside the 60 s window."""
    cid = node["camera_id"]
    url = node.get("snapshot_url")
    if not url:
        return None, None
    if not force and not _gate(cid, config.SNAPSHOT_MIN_INTERVAL_S):
        p = _cached_latest_path(cid)
        if p:
            return p.read_bytes(), latest_record(cid)
        # window not elapsed and nothing cached: wait for the gate next call
        return None, None
    try:
        with _FETCH_SEM:
            r = client().get(url, params={"_": int(time.time() * 1000)})
        if r.status_code != 200 or not r.content.startswith(b"\xff\xd8\xff"):
            return None, None
        ts = time.time()
        stale = is_placeholder(r.content)
        path = None if stale else _store(cid, r.content, ts)
        rec = _emit(cid, node, ts, path, "sdot-snapshot", stale)
        return (None if stale else r.content), rec
    except Exception:
        p = _cached_latest_path(cid)
        return (p.read_bytes(), latest_record(cid)) if p else (None, None)


# --------------------------------------------------------------------- HLS

_URI_RE = re.compile(r"^[^#\s].*$", re.MULTILINE)


def _hls_newest_segment_url(hls_url: str) -> str | None:
    r = client().get(hls_url, timeout=10)
    if r.status_code != 200 or b"#EXTM3U" not in r.content[:64]:
        return None
    lines = _URI_RE.findall(r.text)
    if not lines:
        return None
    target = urljoin(hls_url, lines[-1])
    if target.endswith(".m3u8"):  # master playlist -> chunklist
        r2 = client().get(target, timeout=10)
        if r2.status_code != 200:
            return None
        segs = [l for l in _URI_RE.findall(r2.text) if not l.endswith(".m3u8")]
        if not segs:
            return None
        target = urljoin(target, segs[-1])
    return target


def hls_frame(node: dict) -> tuple[bytes | None, dict | None]:
    """Freshest frame from the camera's live stream: newest TS segment,
    decoded to its last frame. Returns (jpeg, FrameRecord) or (None, None)."""
    cid = node["camera_id"]
    if not node.get("has_stream") or not node.get("hls_url"):
        return None, None
    if not _gate(f"hls:{cid}", config.HLS_MIN_INTERVAL_S):
        p = _cached_latest_path(cid)
        return (p.read_bytes(), latest_record(cid)) if p else (None, None)
    try:
        with _FETCH_SEM:
            seg_url = _hls_newest_segment_url(node["hls_url"])
            if not seg_url:
                return None, None
            seg = client().get(seg_url, timeout=15)
        if seg.status_code != 200 or not seg.content:
            return None, None
        ts = time.time()
        d = config.SEGMENTS / cid
        d.mkdir(parents=True, exist_ok=True)
        seg_path = d / f"{_stamp(ts)}.ts"
        seg_path.write_bytes(seg.content)
        for old in sorted(d.glob("*.ts"))[:-3]:  # keep last 3 segments
            old.unlink(missing_ok=True)

        cap = cv2.VideoCapture(str(seg_path))
        frame = None
        while True:
            ok, f = cap.read()
            if not ok:
                break
            frame = f
        cap.release()
        if frame is None:
            return None, None
        ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 88])
        if not ok:
            return None, None
        blob = buf.tobytes()
        path = _store(cid, blob, ts)
        rec = _emit(cid, node, ts, path, "sdot-hls", stale=False)
        return blob, rec
    except Exception:
        return None, None


# ------------------------------------------------------------- public face

def latest_frame(node: dict, prefer: str = "hls") -> tuple[bytes | None, dict | None]:
    """Best current frame for a node: live stream first (more up to date),
    snapshot fallback. This is the function the sweep and the API share."""
    if prefer == "hls" and node.get("has_stream"):
        blob, rec = hls_frame(node)
        if blob is not None:
            return blob, rec
    return snapshot_frame(node)


def latest_frame_bytes(camera_id: str, nodes: dict[str, dict]) -> bytes | None:
    """Serve-from-cache accessor for GET /frame/<id>/latest.jpg — fetches
    only if the rate gate allows."""
    node = nodes.get(camera_id)
    if node is None:
        return None
    blob, _ = latest_frame(node)
    if blob is not None:
        return blob
    p = _cached_latest_path(camera_id)
    return p.read_bytes() if p else None
