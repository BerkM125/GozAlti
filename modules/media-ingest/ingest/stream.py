"""Live HLS streamers — frames decoded from the stream AS chunks arrive.

Instead of polling for the newest completed TS segment, a CamStreamer holds
the camera's HLS stream open through OpenCV's ffmpeg demuxer (exactly what a
video player does) and drains frames continuously, always keeping the newest
decoded frame in a shared slot. Frame latency drops from "segment cadence +
poll gate" to "encoder latency + network" — this is the real live edge.

Rate discipline, restated precisely: one streamer costs upstream exactly what
ONE ordinary viewer watching the stream costs (every ~2-4 s segment while
open). Hard bounds keep that honest:
  * at most STREAM_MAX streamers at once (LRU eviction),
  * a streamer stops STREAM_IDLE_TTL_S after the last request for its camera,
  * a stalled stream (no frame for STREAM_STALL_S) reopens once, then dies
    and the segment-poll fallback in feeds.py takes over.

Every STREAM_EMIT_EVERY_S a decoded frame is fed back through feeds'
store/emit path, so FrameRecords, the activity flag, and the browser
snapshot pane stay consistent with what the streamer sees.
"""
from __future__ import annotations

import threading
import time

import cv2

from . import config, feeds

_streams: dict[str, "CamStreamer"] = {}
_lock = threading.Lock()


class CamStreamer(threading.Thread):
    def __init__(self, node: dict):
        super().__init__(daemon=True, name=f"stream-{node['camera_id']}")
        self.node = node
        self.cid = node["camera_id"]
        self.url = node.get("hls_url")
        self.last_used = time.monotonic()
        self.stop_flag = threading.Event()
        self.frame = None          # newest BGR ndarray
        self.frame_mono = 0.0      # monotonic time it was decoded
        self.frame_wall = 0.0      # wall time for FrameRecord timestamps
        self.frame_lock = threading.Lock()
        self.opens = 0
        self._last_emit = 0.0

    # ---- consumer side ----------------------------------------------------
    def touch(self) -> None:
        self.last_used = time.monotonic()

    def latest(self, max_age_s: float = 5.0):
        """(bgr_frame, wall_ts) if fresh enough, else (None, None)."""
        with self.frame_lock:
            if self.frame is None or time.monotonic() - self.frame_mono > max_age_s:
                return None, None
            return self.frame, self.frame_wall

    # ---- producer side ----------------------------------------------------
    def run(self) -> None:
        while not self.stop_flag.is_set():
            if time.monotonic() - self.last_used > config.STREAM_IDLE_TTL_S:
                break                                    # nobody watching
            if not self._pump():
                if self.opens >= 2:
                    break                                # twice stalled/failed
                time.sleep(1.0)
        with _lock:
            _streams.pop(self.cid, None)

    def _pump(self) -> bool:
        """One open->drain cycle. Returns False on failure/stall."""
        self.opens += 1
        cap = cv2.VideoCapture(self.url, cv2.CAP_FFMPEG)
        if not cap.isOpened():
            cap.release()
            return False
        try:
            last_frame_at = time.monotonic()
            while not self.stop_flag.is_set():
                if time.monotonic() - self.last_used > config.STREAM_IDLE_TTL_S:
                    return True
                ok, f = cap.read()          # blocks until the next frame arrives
                now = time.monotonic()
                if not ok:
                    return now - last_frame_at < config.STREAM_STALL_S
                last_frame_at = now
                with self.frame_lock:
                    self.frame = f
                    self.frame_mono = now
                    self.frame_wall = time.time()
                self._maybe_emit(f)
        finally:
            cap.release()
        return True

    def _maybe_emit(self, f) -> None:
        """Periodically push a streamed frame through feeds' store/emit path
        so activity flags + FrameRecords + the snapshot pane stay in sync."""
        now = time.monotonic()
        if now - self._last_emit < config.STREAM_EMIT_EVERY_S:
            return
        self._last_emit = now
        try:
            ok, buf = cv2.imencode(".jpg", f, [cv2.IMWRITE_JPEG_QUALITY, 85])
            if ok:
                feeds.ingest_external_frame(self.node, buf.tobytes(),
                                            time.time(), "sdot-hls")
        except Exception:
            pass


def ensure(node: dict) -> CamStreamer | None:
    """Start (or touch) the streamer for a camera. Enforces STREAM_MAX by
    evicting the least-recently-used streamer."""
    if not node.get("has_stream") or not node.get("hls_url"):
        return None
    cid = node["camera_id"]
    with _lock:
        s = _streams.get(cid)
        if s and s.is_alive():
            s.touch()
            return s
        if len(_streams) >= config.STREAM_MAX:
            lru = min(_streams.values(), key=lambda x: x.last_used)
            lru.stop_flag.set()
            _streams.pop(lru.cid, None)
        s = CamStreamer(node)
        _streams[cid] = s
        s.start()
        return s


def latest(cid: str, max_age_s: float = 5.0):
    with _lock:
        s = _streams.get(cid)
    if s is None or not s.is_alive():
        return None, None
    s.touch()
    return s.latest(max_age_s)


def active() -> dict:
    with _lock:
        return {cid: {"alive": s.is_alive(), "opens": s.opens,
                      "frame_age_s": (round(time.monotonic() - s.frame_mono, 1)
                                      if s.frame is not None else None)}
                for cid, s in _streams.items()}


def stop_all() -> None:
    with _lock:
        for s in _streams.values():
            s.stop_flag.set()
        _streams.clear()
