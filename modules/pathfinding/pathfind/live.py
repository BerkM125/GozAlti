"""PathLiveSession — the separate live-updating component.

find_path() is one-and-done and NEVER fetches live data. This component
interacts with the shipped path object: it watches the corridor, pulls live
signals AS THEY ARRIVE, recomputes, and AUTO-REPLACES the path (versioned)
whenever the optimum changes. The UI contract is deliberately tiny:

    POST /api/route/start?...      -> initial PathObject (version 1,
                                      live.incorporated=false) + session
    GET  /api/route/live/{path_id} -> {version, changed_since, path}
                                      poll ~2 s; render when version moves
    DELETE /api/route/live/{path_id} (optional; sessions expire on idle)

What the loop pulls, per tick (~4 s), all rate-limit-safe by construction:
  fresh OpenCV   corridor cameras through cvdetect.analyze_camera_cv — its
                 per-frame cache + hot-prefetcher + frame gates mean polling
                 never adds upstream load
  VLM            detect.analyze_camera per corridor camera, throttled to
                 >=60 s/camera, only if a VLM backend is configured; flags +
                 people counts + captions come back on the camera state
  activity/co-presence  free — already live on the camera graph nodes
  SDOT           if the collisions artifact is missing, ONE background
                 fetch + static rebuild happens here (the "high latency ->
                 live phase" rule), then it's static forever

Occupancy per camera = max(local-CV person count, VLM people_count),
freshest wins. The night rule from core.py is applied per edge via the
cameras that cover it.
"""
from __future__ import annotations

import threading
import time

from . import core
from .core import REPO, W, graph, camera_graph, find_path, is_night

import sys
sys.path.insert(0, str(REPO / "modules" / "media-ingest"))
from ingest import cvdetect, detect, observations, vlm_client   # noqa: E402

TICK_S = 4.0
SESSION_TTL_S = 180.0
VLM_MIN_INTERVAL_S = 60.0

_sessions: dict[str, "PathLiveSession"] = {}
_lock = threading.Lock()


class PathLiveSession(threading.Thread):
    def __init__(self, path: dict, kind: str,
                 o: tuple[float, float], d: tuple[float, float]):
        super().__init__(daemon=True, name=f"path-{path['path_id']}")
        self.path = path
        self.kind = kind
        self.o, self.d = o, d
        self.version = path["version"]
        self.last_poll = time.monotonic()
        self.stop_flag = threading.Event()
        self.lock = threading.Lock()
        self._vlm_last: dict[str, float] = {}
        self._layers_done: set[str] = set()

    # ------------------------------------------------------------ polling
    def snapshot(self, since: int | None) -> dict:
        self.last_poll = time.monotonic()
        with self.lock:
            return {"version": self.version,
                    "changed_since": since is None or self.version > since,
                    "path": self.path}

    # ------------------------------------------------------------ the loop
    def run(self) -> None:
        while not self.stop_flag.is_set():
            if time.monotonic() - self.last_poll > SESSION_TTL_S:
                break
            try:
                self._tick()
            except Exception:
                import traceback
                print(f"[pathfind.live] tick failed for {self.path['path_id']}:",
                      file=__import__('sys').stderr)
                traceback.print_exc()
            time.sleep(TICK_S)
        with _lock:
            _sessions.pop(self.path["path_id"], None)

    def _corridor_cams(self) -> list[str]:
        # cameras_en_route ships as detail dicts (find_path) — extract ids
        return [c["camera_id"] if isinstance(c, dict) else c
                for c in self.path["cameras_en_route"]]

    def _tick(self) -> None:
        g = graph()
        cg = camera_graph()

        # 1. SDOT backfill (once, only if the artifact was pending)
        if "sdot-collisions" in g.static_meta.get("layers_pending", []) \
                and "sdot" not in self._layers_done:
            self._layers_done.add("sdot")     # one attempt per session
            from . import build_static
            threading.Thread(target=build_static.build, daemon=True).start()

        night = is_night(*self.o)
        cam_occ: dict[str, tuple[int, str]] = {}   # cid -> (people, source)
        vlm_flagged: set[str] = set()
        cv_results = {}

        vlm_on = vlm_client.available()
        cids = [c for c in self._corridor_cams() if c in cg.nodes]

        # THE RULE OF THIS LOOP: never fetch inline. Mark corridor cameras
        # hot — cvdetect's prefetcher does all fetching+inference at its own
        # proven rate-safe cadence — and READ whatever has landed. The tick
        # itself is read-only assembly + a fast A*.
        for cid in cids:
            cvdetect.mark_hot(cg.nodes[cid])
            r = cvdetect.cached_result(cid)
            if r and r.get("ok"):
                cv_results[cid] = r
                people = sum(1 for det in r["detections"]
                             if det["label"] == "person")
                cam_occ[cid] = (people, "opencv")
                self._layers_done.add("opencv")

        for cid in cids:
            # VLM: fire-and-forget in a bounded thread, >=60 s per camera
            now = time.monotonic()
            if vlm_on and now - self._vlm_last.get(cid, 0) > VLM_MIN_INTERVAL_S:
                self._vlm_last[cid] = now
                threading.Thread(target=detect.analyze_camera,
                                 args=(cg, cid), daemon=True).start()
            live = detect.live_state(cid)
            if live and live.get("ok"):
                v_people = sum(1 for det in live.get("detections", [])
                               if det.get("label") == "person")
                if cid not in cam_occ or v_people > cam_occ[cid][0]:
                    cam_occ[cid] = (v_people, "vlm")
                self._layers_done.add("vlm")
            for obs in observations.priors(cid)[-1:]:
                if obs.get("flags"):
                    vlm_flagged.add(cid)
                pc = obs.get("people_count")
                if pc is not None and (cid not in cam_occ or pc > cam_occ[cid][0]):
                    cam_occ[cid] = (pc, "vlm-observation")
                    self._layers_done.add("vlm")

        # 2. per-edge live overlay from covering cameras (the night rule)
        overlay: dict[int, float] = {}
        parts: dict[int, dict] = {}
        if g.static:
            day_scale = 1.0 if night else core.DAY_SCALE
            for ei, st in enumerate(g.static):
                cams = [c for c in st.get("cams", []) if c in cam_occ]
                if not cams:
                    continue
                people = max(cam_occ[c][0] for c in cams)
                occ = 0.0 if people >= 3 else 1.0 if people >= 1 else 0.5
                p = {"occupancy": W["occupancy"] * occ * day_scale,
                     "vlm_flags": W["vlm_flags"]
                     * (1.0 if any(c in vlm_flagged for c in cams) else 0.0)}
                overlay[ei] = p["occupancy"] + p["vlm_flags"]
                parts[ei] = p

        # 3. re-route with the overlay INSIDE the search; auto-replace
        new = find_path(self.o[0], self.o[1], self.d[0], self.d[1],
                        self.kind, live_overlay=overlay, live_parts=parts,
                        _version=self.version + 1)
        new["path_id"] = self.path["path_id"]
        new["cv_detections"] = cv_results or new["cv_detections"]
        new["live"] = {
            "incorporated": bool(self._layers_done),
            "basis": "deterministic + live overlay in-search",
            "layers_incorporated": sorted(self._layers_done),
            "layers_pending": [x for x in
                               ("opencv", "vlm", "sdot")
                               if x not in self._layers_done
                               and not (x == "sdot" and "sdot-collisions"
                                        not in graph().static_meta["layers_pending"])],
            "cameras_reporting": {c: {"people": n, "source": s}
                                  for c, (n, s) in cam_occ.items()},
        }
        occ_now = {c: n for c, (n, _s) in cam_occ.items()}
        occ_moved = occ_now != getattr(self, "_last_occ", None)
        self._last_occ = occ_now
        with self.lock:
            changed = (new["polyline"] != self.path["polyline"]
                       or [s["risk"] for s in new["segments"]]
                       != [s["risk"] for s in self.path["segments"]]
                       or new["live"]["incorporated"]
                       != self.path["live"]["incorporated"])
            if changed:
                self.version += 1
                new["version"] = self.version
                self.path = new
            elif occ_moved:
                # camera evidence moved without changing the optimum: refresh
                # the reporting block in place (no version bump) so summaries
                # revise with the freshest counts
                self.path["live"]["cameras_reporting"] = \
                    new["live"]["cameras_reporting"]
        if changed or occ_moved:
            # LLM phrasing of the fresh evidence (queued; coalesces on
            # unchanged segments, revises the ones whose evidence moved)
            from . import summarize
            summarize.enqueue_from_path(self.path)


def start_session(olat, olon, dlat, dlon, kind="safer") -> dict:
    """find_path + begin live updating. Returns the version-1 PathObject."""
    p = find_path(olat, olon, dlat, dlon, kind)
    from . import summarize
    summarize.enqueue_from_path(p)        # deterministic-layer summaries now
    s = PathLiveSession(p, kind, (olat, olon), (dlat, dlon))
    with _lock:
        _sessions[p["path_id"]] = s
    s.start()
    return p


def get_session(path_id: str) -> PathLiveSession | None:
    with _lock:
        return _sessions.get(path_id)


def stop_session(path_id: str) -> bool:
    with _lock:
        s = _sessions.pop(path_id, None)
    if s:
        s.stop_flag.set()
        return True
    return False
