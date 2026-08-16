"""The router core: graph, cost model, A*, and the one-and-done find_path().

Graph: harness's data/walk_graph.json (real OSM walkable streets; bbox
expanded 16 Aug to downtown + U-District/UW — see scripts/build-graph.py
there) — loaded verbatim, never edited.

COST MODEL (documented here, in the response `risk_basis`, and in SPEC.md):

    cost(edge) = length_m * (1 + 3.0 * min(risk, 1.2))

`risk` is a weighted sum of parts, each normalized to [0,1] BEFORE its
weight (audit note: the multiplicative meters x dimensionless form is
harness's, kept as-is; the capped sum fixes harness's uncapped-search nit).

STATIC parts (in the one-and-done path — all precomputed per edge):
    traffic    0.16  OSM road class (harness's table)
    lighting   0.14  lit tag / class prior, x night factor
    sidewalk   0.14  OSM sidewalk tags (harness's logic)
    collisions 0.24  REAL SDOT pedestrian/cyclist collision density per
                     100 m (ArcGIS SDOT_Collisions_All_Years_1, safe-walk's
                     query, cached artifact). Artifact absent -> part = 0
                     and "sdot-collisions" listed in layers_pending; the
                     live session fetches it. NEVER fabricated.
    coverage   0.16  camera-coverage gap: nearest camera >120 m from the
                     edge -> 1.0, <=40 m -> 0.0, linear between; -0.15
                     bonus per extra camera within 100 m (floor 0)
    osint      0.10  Dhruv's AreaSignals (signals.latest.json §6.3):
                     (1 - sentiment)/2 x confidence for areas within
                     400 m; no signals yet -> 0 + flagged unavailable
    crossings  0.06  junction density (harness's neutral 0.5 in search)

LIVE parts (added by live.py to corridor edges only; re-route auto-replaces):
    occupancy  0.25  the night rule, exactly as specified: at night a
                     street where cameras see MANY (>=3) people = 0.0,
                     NO motion in a while = 0.5, ONE-OR-TWO people = 1.0.
                     Day: x0.15 ("risk either way goes down heavily").
    vlm_flags  0.10  any VLM flag (e.g. blocked_sidewalk) on a covering
                     camera = 1.0 (not day-scaled — a blocked sidewalk
                     matters in daylight too)

NIGHT = local hour >= 20 ("night time is after 8pm") OR sun below horizon
(NOAA) — so 5 AM darkness counts, noon never does. Day scaling (x0.15)
applies to lighting + occupancy only.
"""
from __future__ import annotations

import heapq
import json
import math
import sys
import time
import uuid
from pathlib import Path

MODULE_ROOT = Path(__file__).resolve().parent.parent
REPO = MODULE_ROOT.parent.parent
DATA = MODULE_ROOT / "data"
DATA.mkdir(exist_ok=True)

sys.path.insert(0, str(REPO / "modules" / "media-ingest"))
from ingest import solar                                    # noqa: E402
from ingest.graph import CameraGraph, to_xy                 # noqa: E402

RISK_WEIGHT = 3.0
RISK_CAP = 1.2
DETOUR_CAP = 1.25
WALK_M_PER_MIN = 80.0

W = {"traffic": 0.16, "lighting": 0.14, "sidewalk": 0.14, "collisions": 0.24,
     "coverage": 0.16, "osint": 0.10, "crossings": 0.06,
     "occupancy": 0.25, "vlm_flags": 0.10}
DAY_SCALE = 0.15          # applied to lighting + occupancy when not night

_TRAFFIC = {"primary": 0.95, "primary_link": 0.95, "secondary": 0.72,
            "secondary_link": 0.72, "tertiary": 0.50, "tertiary_link": 0.50,
            "residential": 0.26, "unclassified": 0.30, "living_street": 0.15,
            "service": 0.28, "track": 0.30, "road": 0.40, "cycleway": 0.18,
            "steps": 0.20, "path": 0.12, "footway": 0.10, "pedestrian": 0.08}
_LIT_PRIOR = {"primary": 0.25, "secondary": 0.30, "tertiary": 0.38,
              "residential": 0.50, "living_street": 0.45, "service": 0.62,
              "footway": 0.55, "path": 0.78, "pedestrian": 0.35,
              "steps": 0.60, "cycleway": 0.62}

RISK_BASIS = (
    "cost = m x (1 + 3.0 x min(risk,1.2)); static parts (weightxrange01): "
    "traffic .16 osm-class | lighting .14 lit-tag/prior (x0.15 day) | "
    "sidewalk .14 osm-tags | collisions .24 REAL-SDOT-density (0+pending if "
    "artifact not yet fetched) | coverage .16 camera-gap | osint .10 "
    "AreaSignals-or-0 | crossings .06; live parts on corridor: occupancy .25 "
    "night-rule[many-people 0.0 / no-motion 0.5 / 1-2-people 1.0; x0.15 day] "
    "| vlm_flags .10. night = after 20:00 local or sun below horizon (NOAA). "
    "Mechanical, deterministic, every part carries its source; not a "
    "synthesis verdict.")


class _G:
    """Loaded graph + static overlay, singleton."""
    def __init__(self):
        g = json.loads((REPO / "modules" / "harness" / "data" /
                        "walk_graph.json").read_text())
        self.nodes = g["nodes"]            # [ [lon, lat], ... ]
        self.edges = g["edges"]            # [ [a, b, len_m, way_i], ... ]
        self.adj = g["adj"]                # node -> [edge_i, ...]
        self.ways = g["ways"]
        self.xy = [to_xy(lat, lon) for lon, lat in self.nodes]
        # spatial cell index of nodes for snapping
        self.cell = {}
        for i, (x, y) in enumerate(self.xy):
            self.cell.setdefault((int(x // 250), int(y // 250)), []).append(i)
        # static overlay (built by build_static.py); absent -> zeros + pending
        p = DATA / "edge_static.json"
        if p.exists():
            s = json.loads(p.read_text())
            # alignment guard: the overlay is positional per edge — a graph
            # rebuilt with a different bbox/edge count makes it WRONG data,
            # not stale data. Refuse it rather than mis-weight edges.
            if len(s["edges"]) == len(self.edges):
                self.static = s["edges"]   # edge_i -> {coverage, collisions, osint, cams}
                self.static_meta = s["meta"]
            else:
                self.static = None
                self.static_meta = {"layers_pending":
                                    ["edge-static-build (graph changed — "
                                     "rerun python -m pathfind.build_static)"]}
        else:
            self.static = None
            self.static_meta = {"layers_pending": ["edge-static-build"]}

    def base_parts(self, ei: int, night: bool) -> dict:
        a, b, length, wi = self.edges[ei]
        w = self.ways[wi]
        hw = w["highway"]
        lit = w.get("lit") or ""
        light = 0.10 if lit == "yes" else 0.85 if lit == "no" \
            else _LIT_PRIOR.get(hw, 0.50)
        sw = w.get("sidewalk") or ""
        if hw in ("footway", "pedestrian", "path", "steps"):
            walk = 0.06
        elif sw in ("both", "left", "right", "yes"):
            walk = 0.12
        elif sw == "no":
            walk = 0.85
        else:
            walk = 0.42
        st = self.static[ei] if self.static else {}
        parts = {
            "traffic": W["traffic"] * _TRAFFIC.get(hw, 0.35),
            "lighting": W["lighting"] * light * (1.0 if night else DAY_SCALE),
            "sidewalk": W["sidewalk"] * walk,
            "collisions": W["collisions"] * st.get("collisions", 0.0),
            "coverage": W["coverage"] * st.get("coverage", 0.0),
            "osint": W["osint"] * st.get("osint", 0.0),
            "crossings": W["crossings"] * 0.5,
        }
        return parts


_g: _G | None = None
_cams: CameraGraph | None = None


def graph() -> _G:
    global _g
    if _g is None:
        _g = _G()
    return _g


def camera_graph() -> CameraGraph:
    global _cams
    if _cams is None:
        _cams = CameraGraph.load()
    return _cams


def is_night(lat: float, lon: float) -> bool:
    if time.localtime().tm_hour >= 20:
        return True
    _, el = solar.solar_position(lat, lon, time.time())
    return el < 0


def _snap(g: _G, lat: float, lon: float) -> int:
    x, y = to_xy(lat, lon)
    cx, cy = int(x // 250), int(y // 250)
    best, bd = -1, 1e18
    for dx in (-1, 0, 1):
        for dy in (-1, 0, 1):
            for i in g.cell.get((cx + dx, cy + dy), []):
                d = (g.xy[i][0] - x) ** 2 + (g.xy[i][1] - y) ** 2
                if d < bd:
                    best, bd = i, d
    return best if bd < 2000.0 ** 2 else -1


def _astar(g: _G, src: int, dst: int, night: bool,
           live_overlay: dict[int, float] | None = None,
           weighted: bool = True) -> list[int] | None:
    """Returns list of edge indices. live_overlay: edge_i -> extra live risk
    (already weighted) added on top of the static parts."""
    tx, ty = g.xy[dst]

    def h(n):
        return math.hypot(g.xy[n][0] - tx, g.xy[n][1] - ty)

    dist = {src: 0.0}
    prev: dict[int, tuple[int, int]] = {}
    pq = [(h(src), src)]
    seen = set()
    while pq:
        _, u = heapq.heappop(pq)
        if u == dst:
            break
        if u in seen:
            continue
        seen.add(u)
        for ei in g.adj[u]:
            a, b, length, wi = g.edges[ei]
            v = b if a == u else a
            if weighted:
                risk = sum(g.base_parts(ei, night).values())
                if live_overlay:
                    risk += live_overlay.get(ei, 0.0)
                step = length * (1.0 + RISK_WEIGHT * min(risk, RISK_CAP))
            else:
                step = length
            nd = dist[u] + step
            if nd < dist.get(v, 1e18):
                dist[v] = nd
                prev[v] = (u, ei)
                heapq.heappush(pq, (nd + h(v), v))
    if dst not in prev and src != dst:
        return None
    out, n = [], dst
    while n != src:
        u, ei = prev[n]
        out.append(ei)
        n = u
    return list(reversed(out))


def _bucket(r: float) -> str:
    return "low" if r < 0.35 else "medium" if r < 0.65 else "high"


def _build_segments(g: _G, path: list[int], src: int, night: bool,
                    live_overlay: dict[int, float] | None,
                    live_parts: dict[int, dict] | None) -> list[dict]:
    """Merge consecutive same-way edges into UI segments with full parts."""
    runs, n = [], src
    for ei in path:
        a, b, length, wi = g.edges[ei]
        nxt = b if a == n else a
        if runs and runs[-1]["wi"] == wi:
            runs[-1]["nodes"].append(nxt)
            runs[-1]["eis"].append(ei)
            runs[-1]["m"] += length
        else:
            runs.append({"wi": wi, "nodes": [n, nxt], "eis": [ei], "m": length})
        n = nxt
    segs = []
    for r in runs:
        w = g.ways[r["wi"]]
        parts = {k: 0.0 for k in
                 ("traffic", "lighting", "sidewalk", "collisions",
                  "coverage", "osint", "crossings")}
        live_p = {"occupancy": 0.0, "vlm_flags": 0.0}
        cams: set[str] = set()
        for ei in r["eis"]:
            bp = g.base_parts(ei, night)
            for k, v in bp.items():
                parts[k] += v * g.edges[ei][2]
            if live_parts and ei in live_parts:
                for k in live_p:
                    live_p[k] += live_parts[ei].get(k, 0.0) * g.edges[ei][2]
            if g.static:
                cams.update(g.static[ei].get("cams", []))
        for k in parts:
            parts[k] = round(parts[k] / r["m"], 3)
        for k in live_p:
            live_p[k] = round(live_p[k] / r["m"], 3)
        base = min(sum(parts.values()), 1.0)
        risk = min(base + sum(live_p.values()), 1.0)
        segs.append({
            "segment_id": f"pf:{w['id']}-{r['nodes'][0]}",
            "name": w["name"] or w["highway"],
            "geometry": {"type": "LineString",
                         "coordinates": [g.nodes[i] for i in r["nodes"]]},
            "length_m": round(r["m"]),
            "risk": round(risk, 2),
            "live_risk": round(risk, 2),         # UI-compat alias
            "base_risk": round(base, 2),         # static-only portion
            "risk_bucket": _bucket(risk),
            "risk_parts": {**parts, **live_p},   # every part, named + weighted
            "cameras": sorted(cams),
        })
    return segs


def find_path(olat: float, olon: float, dlat: float, dlon: float,
              kind: str = "safer",
              live_overlay: dict[int, float] | None = None,
              live_parts: dict[int, dict] | None = None,
              _version: int = 1) -> dict:
    """THE one-and-done function. Deterministic layers + whatever static
    artifacts exist; cached OpenCV shipped alongside; NO live fetches, no
    VLM/VSS calls — live.py owns those. Raises ValueError(code)."""
    g = graph()
    t0 = time.monotonic()
    src, dst = _snap(g, olat, olon), _snap(g, dlat, dlon)
    if src < 0 or dst < 0:
        raise ValueError("out_of_area")
    if src == dst:
        raise ValueError("too_close")
    night = is_night(olat, olon)

    path = _astar(g, src, dst, night, live_overlay,
                  weighted=(kind == "safer"))
    if path is None:
        raise ValueError("no_route")
    direct = _astar(g, src, dst, night, weighted=False)
    direct_m = sum(g.edges[ei][2] for ei in direct) if direct else 0
    total_m = sum(g.edges[ei][2] for ei in path)
    if kind == "safer" and direct_m and total_m / direct_m > DETOUR_CAP:
        path = direct                     # honest fallback, flagged below
        total_m = direct_m
        capped = True
    else:
        capped = False

    coords, n = [g.nodes[src]], src
    for ei in path:
        a, b, _, _ = g.edges[ei]
        n = b if a == n else a
        coords.append(g.nodes[n])

    segs = _build_segments(g, path, src, night, live_overlay, live_parts)

    # en-route cameras with live module state + cached CV, shipped with path
    cg = camera_graph()
    sys.path.insert(0, str(REPO / "modules" / "media-ingest"))
    from ingest import activity as act_mod, cvdetect, refuge
    cam_ids: list[str] = []
    for s in segs:
        for c in s["cameras"]:
            if c not in cam_ids:
                cam_ids.append(c)
    cams_detail, cv_cached = [], {}
    for cid in cam_ids:
        node = cg.nodes.get(cid)
        if not node:
            continue
        a = act_mod.effective_activity(node)
        cams_detail.append({
            "camera_id": cid, "lat": node["lat"], "lon": node["lon"],
            "location_desc": node.get("location_desc"),
            "has_stream": bool(node.get("has_stream")),
            "active": a.get("active") if a else None,
            "last_person_at": (node.get("copresence") or {}).get("last_person_at"),
        })
        with cvdetect._cache_lock:
            r = cvdetect._cache.get(cid)
        if r and r.get("ok"):
            cv_cached[cid] = r            # cached ONLY — age visible in frame_ts

    # open refuges along the walk
    exits: dict[str, dict] = {}
    step = max(1, len(coords) // 20)
    for lon, lat in coords[::step] + [coords[-1]]:
        res = refuge.near(lat, lon, 60.0)
        if not res.get("available"):
            break
        for p in res["pois"]:
            if p["open_now"] is True and (p["osm_id"] not in exits
                                          or p["dist_m"] < exits[p["osm_id"]]["dist_m"]):
                exits[p["osm_id"]] = p

    pending = list(g.static_meta.get("layers_pending", []))
    n_medium_up = sum(1 for s in segs if s["risk_bucket"] != "low")
    return {
        "path_id": f"p-{uuid.uuid4().hex[:10]}",
        "version": _version,
        "kind": kind,
        "live": {
            "incorporated": _version > 1,
            "basis": ("deterministic + cached-opencv" if _version == 1
                      else "deterministic + live opencv/vlm overlay"),
            "layers_pending": pending + (
                ["vlm-observations", "fresh-opencv"] if _version == 1 else []),
        },
        "night": night,
        "daylight": not night,            # UI-compat alias
        "detour_cap_hit": capped,
        "polyline": [[lat, lon] for lon, lat in coords],
        "length_m": round(total_m),
        "eta_min": round(total_m / WALK_M_PER_MIN, 1),
        "segments": segs,
        "cameras_en_route": [c["camera_id"] for c in cams_detail],
        "cameras_en_route_detail": cams_detail,
        "cv_detections": cv_cached,       # shipped WITH the path (cached)
        "refuges_en_route": sorted(exits.values(), key=lambda p: p["dist_m"])[:30],
        "evidence_summary": (
            f"{'safer' if kind == 'safer' else 'shortest'} A*: "
            f"{n_medium_up} of {len(segs)} segment(s) medium+ risk; "
            f"{len(cams_detail)} camera(s) en route"
            + (f"; detour cap hit — direct route served" if capped else "")),
        "risk_basis": RISK_BASIS,
        "compute_ms": round((time.monotonic() - t0) * 1000),
    }
