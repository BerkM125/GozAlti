"""Deterministic pathfinding over Seattle's walkable street graph.

Ported from the safe-walk prototype's A* router (~/Desktop/safe-walk/server/router.py).
A* for the "shortest" kind; the same search under a risk multiplier for "safer", so
both follow real streets.

The risk model is deliberately coarse. Traffic exposure and sidewalk presence come
from OSM tags; lighting falls back to a class prior where `lit` is absent; collisions
have no data source here and are synthesized deterministically from the way id.
Swap `_risk_parts()` for real layers (SegmentAssessment.risk from synthesis, once it
exists) without touching the search.

    python3 scripts/build-graph.py     # writes data/walk_graph.json once
"""

import heapq
import json
import pathlib

from ._geo import haversine
from .cameras import cameras_for

ROOT = pathlib.Path(__file__).resolve().parent.parent
GRAPH_PATH = ROOT / "data" / "walk_graph.json"

WALK_M_PER_MIN = 80.0
RISK_WEIGHT = 3.0  # how hard "safer" leans away from risky edges
MIN_SEGMENT_M = 60.0  # shorter runs get merged into the previous segment
MAX_SEGMENTS = 12
MAX_SNAP_M = 2000.0
DETOUR_CAP = 1.25  # "safer" over this many times the direct distance is refused
EN_ROUTE_RADIUS_M = 60.0  # how close a camera must sit to a walked point to count
EN_ROUTE_STRIDE = 3  # sample every Nth path coordinate — cheap, dense enough

# Readable labels for ways OSM leaves unnamed (mostly sidewalks and crossings).
_UNNAMED = {
    "footway": "Sidewalk", "path": "Path", "steps": "Steps",
    "pedestrian": "Pedestrian way", "service": "Service road", "cycleway": "Cycleway",
}

# Traffic exposure by road class.
_TRAFFIC = {
    "primary": 0.95, "primary_link": 0.95, "secondary": 0.72, "secondary_link": 0.72,
    "tertiary": 0.50, "tertiary_link": 0.50, "residential": 0.26, "unclassified": 0.30,
    "living_street": 0.15, "service": 0.28, "track": 0.30, "road": 0.40,
    "cycleway": 0.18, "steps": 0.20, "path": 0.12, "footway": 0.10, "pedestrian": 0.08,
}
# Prior for lighting when OSM has no `lit` tag.
_LIT_PRIOR = {
    "primary": 0.25, "secondary": 0.30, "tertiary": 0.38, "residential": 0.50,
    "living_street": 0.45, "service": 0.62, "footway": 0.55, "path": 0.78,
    "pedestrian": 0.35, "steps": 0.60, "cycleway": 0.62,
}

_KIND_TO_PROFILE = {"shortest": "direct", "safer": "safer"}


class RouteError(ValueError):
    """Raised with a machine-readable code: out_of_area, too_close, no_route,
    unknown_kind, or detour_cap_exceeded. `.detail` carries extra fields for
    callers that need them (currently just detour_ratio/cap on the cap error)."""

    def __init__(self, code: str, **detail):
        super().__init__(code)
        self.code = code
        self.detail = detail


def _jitter(seed: int, lo: float, hi: float) -> float:
    """Stable pseudo-random in [lo, hi] — same way always yields the same value."""
    x = (seed * 2654435761) % 4294967296
    x ^= x >> 13
    x = (x * 1274126177) % 4294967296
    return lo + (x / 4294967296) * (hi - lo)


class _Router:
    def __init__(self, path: pathlib.Path = GRAPH_PATH):
        g = json.loads(path.read_text())
        self.nodes = g["nodes"]
        self.edges = g["edges"]
        self.adj = g["adj"]
        self.ways = g["ways"]
        self.bbox = g["bbox"]
        self._risk_cache: list[float | None] = [None] * len(self.ways)
        # Coarse grid so snapping does not scan 34k nodes.
        self._cell = 0.002
        self._grid: dict[tuple[int, int], list[int]] = {}
        for i, (lng, lat) in enumerate(self.nodes):
            self._grid.setdefault(self._key(lng, lat), []).append(i)

    def _key(self, lng: float, lat: float) -> tuple[int, int]:
        return (int(lng / self._cell), int(lat / self._cell))

    def snap(self, pt) -> tuple[int, float]:
        lng, lat = pt
        cx, cy = self._key(lng, lat)
        for ring in range(1, 40):
            best, best_d = -1, float("inf")
            for gx in range(cx - ring, cx + ring + 1):
                for gy in range(cy - ring, cy + ring + 1):
                    for i in self._grid.get((gx, gy), ()):
                        d = haversine(pt, self.nodes[i])
                        if d < best_d:
                            best, best_d = i, d
            if best >= 0:
                return best, best_d
        return -1, float("inf")

    def _way_risk(self, wi: int) -> float:
        """Search-time risk for a way, cached — this runs on every edge relaxation."""
        r = self._risk_cache[wi]
        if r is None:
            r = self._risk_cache[wi] = sum(self._risk_parts(wi, 0.5).values())
        return r

    def _risk_parts(self, wi: int, crossing_load: float) -> dict:
        w = self.ways[wi]
        hw = w["highway"]
        traffic = _TRAFFIC.get(hw, 0.35)

        lit = w["lit"]
        if lit == "yes":
            light = 0.10
        elif lit == "no":
            light = 0.85
        else:
            light = _LIT_PRIOR.get(hw, 0.50)

        sw = w["sidewalk"]
        if hw in ("footway", "pedestrian", "path", "steps"):
            walk = 0.06
        elif sw in ("both", "left", "right", "yes"):
            walk = 0.12
        elif sw == "no":
            walk = 0.85
        else:
            walk = 0.42

        collisions = _jitter(w["id"], 0.05, 0.55) * (0.5 + traffic)

        return {
            "lighting": round(0.22 * light, 3),
            "sidewalk": round(0.18 * walk, 3),
            "crossings": round(0.18 * min(crossing_load, 1.0), 3),
            "traffic": round(0.25 * traffic, 3),
            "collisions": round(0.17 * min(collisions, 1.0), 3),
        }

    def route(self, src: int, dst: int, profile: str) -> list[int] | None:
        """A* over edge indices. Returns the edge path, or None if unreachable."""
        risky = profile == "safer"
        target = self.nodes[dst]
        cost = {src: 0.0}
        came: dict[int, tuple[int, int]] = {}
        pq = [(haversine(self.nodes[src], target), src)]
        done = set()

        while pq:
            _, u = heapq.heappop(pq)
            if u == dst:
                break
            if u in done:
                continue
            done.add(u)
            cu = cost[u]
            for ei in self.adj[u]:
                a, b, d, wi = self.edges[ei]
                v = b if a == u else a
                if v in done:
                    continue
                # Multiplier stays >= 1 so straight-line distance remains admissible.
                step = d * (1.0 + RISK_WEIGHT * self._way_risk(wi)) if risky else d
                nc = cu + step
                if nc < cost.get(v, float("inf")):
                    cost[v] = nc
                    came[v] = (u, ei)
                    heapq.heappush(pq, (nc + haversine(self.nodes[v], target), v))

        if dst not in came and src != dst:
            return None
        path, cur = [], dst
        while cur != src:
            prev, ei = came[cur]
            path.append(ei)
            cur = prev
        path.reverse()
        return path

    def _named_junction(self, node: int) -> bool:
        if len(self.adj[node]) <= 2:
            return False
        return any(self.ways[self.edges[ei][3]]["name"] for ei in self.adj[node])

    def _cross_street(self, node: int, exclude: str) -> str:
        names = set()
        for ei in self.adj[node]:
            n = self.ways[self.edges[ei][3]]["name"]
            if n and n != exclude:
                names.add(n)
        return sorted(names)[0] if names else ""

    def _coords(self, path: list[int], src: int) -> list[list[float]]:
        pts, cur = [self.nodes[src]], src
        for ei in path:
            a, b, _, _ = self.edges[ei]
            cur = b if a == cur else a
            pts.append(self.nodes[cur])
        return [list(p) for p in pts]

    def build_segments(self, path: list[int], src: int) -> list[dict]:
        """Group consecutive edges that share a street into tappable segments."""
        runs: list[dict] = []
        cur = src
        for ei in path:
            a, b, d, wi = self.edges[ei]
            nxt = b if a == cur else a
            # Downtown sidewalks are mapped as separate unnamed footways; keying
            # them all alike keeps a walk down one street from splintering.
            key = self.ways[wi]["name"] or "·unnamed·"
            # Break at street crossings too, or a long sidewalk run would come
            # back as one untappable segment spanning the whole walk.
            split = not runs or runs[-1]["key"] != key or (
                runs[-1]["m"] >= MIN_SEGMENT_M and self._named_junction(cur)
            )
            if split:
                runs.append({"key": key, "wi": wi, "nodes": [cur, nxt], "m": d})
            else:
                runs[-1]["nodes"].append(nxt)
                runs[-1]["m"] += d
            cur = nxt

        merged: list[dict] = []
        for r in runs:
            if merged and r["m"] < MIN_SEGMENT_M:
                merged[-1]["nodes"].extend(r["nodes"][1:])
                merged[-1]["m"] += r["m"]
            else:
                merged.append(r)

        # A sheet you can thumb through beats a faithful edge dump.
        while len(merged) > MAX_SEGMENTS:
            i = min(range(len(merged)), key=lambda j: merged[j]["m"])
            j = i - 1 if i > 0 else 1
            lo, hi = min(i, j), max(i, j)
            merged[lo]["nodes"].extend(merged[hi]["nodes"][1:])
            merged[lo]["m"] += merged[hi]["m"]
            merged.pop(hi)

        segments = []
        for i, r in enumerate(merged):
            w = self.ways[r["wi"]]
            street = w["name"] or _UNNAMED.get(w["highway"], "Walkway")
            a = self._cross_street(r["nodes"][0], street)
            b = self._cross_street(r["nodes"][-1], street)
            name = f"{street}, {a} → {b}" if a and b else street
            # More intersections along a run means more road crossings.
            junctions = sum(1 for n in r["nodes"] if len(self.adj[n]) > 2)
            load = junctions / max(r["m"] / 120.0, 1.0)
            parts = self._risk_parts(r["wi"], load)
            segments.append({
                # "sw:" prefix per SPEC.md §5 (harness) convention; way id + start
                # node keeps it stable across repeat queries of the same street.
                "segment_id": f"sw:{w['id']}-{r['nodes'][0]}",
                "name": name,
                "geometry": {
                    "type": "LineString",
                    "coordinates": [list(self.nodes[n]) for n in r["nodes"]],
                },
                "base_risk": round(min(sum(parts.values()), 1.0), 2),
                "risk_parts": parts,
                "live_penalty": round(_jitter(w["id"] + 7, -0.05, 0.03), 3),
                "confidence": round(_jitter(w["id"] + 13, 0.40, 0.90), 2),
                "stale": _jitter(w["id"] + 29, 0.0, 1.0) < 0.15,
            })
        return segments

    def plan(self, origin, dest, profile: str) -> dict:
        """Raises RouteError with a machine-readable code."""
        s, ds = self.snap(origin)
        t, dt = self.snap(dest)
        if s < 0 or t < 0 or ds > MAX_SNAP_M or dt > MAX_SNAP_M:
            raise RouteError("out_of_area")
        if s == t:
            raise RouteError("too_close")

        path = self.route(s, t, profile)
        if path is None:
            raise RouteError("no_route")

        coords = self._coords(path, s)
        metres = sum(self.edges[ei][2] for ei in path)
        return {
            "total_m": round(metres),
            "geometry": {"type": "LineString", "coordinates": coords},
            "segments": self.build_segments(path, s) if profile == "safer" else [],
        }


_ROUTER: _Router | None = None


def _router() -> _Router:
    global _ROUTER
    if _ROUTER is None:
        _ROUTER = _Router()
    return _ROUTER


def _evidence_summary(kind: str, detour_ratio: float, segments: list[dict]) -> str:
    if kind != "safer" or not segments:
        return "shortest path, no risk weighting"
    avoided = sum(1 for s in segments if s["base_risk"] >= 0.35)
    return (
        f"weighted away from {avoided} of {len(segments)} higher-risk segment(s); "
        f"{detour_ratio:.2f}x the direct distance"
    )


def route(origin: tuple[float, float], dest: tuple[float, float], kind: str = "safer") -> dict:
    """Contract entry point for Route (SPEC.md §6.6).

    origin/dest are (lon, lat) — WGS84, matching GeoJSON/MapLibre convention.
    Raises RouteError with .args[0] in {"out_of_area", "too_close", "no_route",
    "unknown_kind", "detour_cap_exceeded"}.
    """
    if kind not in _KIND_TO_PROFILE:
        raise RouteError("unknown_kind")

    r = _router()
    raw = r.plan(origin, dest, _KIND_TO_PROFILE[kind])

    if kind == "safer":
        direct_m = r.plan(origin, dest, "direct")["total_m"]
        detour_ratio = round(raw["total_m"] / direct_m, 2) if direct_m else 1.0
        if detour_ratio > DETOUR_CAP:
            raise RouteError("detour_cap_exceeded", detour_ratio=detour_ratio, cap=DETOUR_CAP)
    else:
        detour_ratio = 1.0

    segments = raw["segments"]
    coords = raw["geometry"]["coordinates"]
    polyline = [[lat, lon] for lon, lat in coords]

    # Cameras that would actually see this walk: sample the path rather than
    # every point (650-camera scan per sample is cheap; per-point is not).
    sampled = coords[::EN_ROUTE_STRIDE]
    if coords[-1] not in sampled:
        sampled.append(coords[-1])
    cameras_en_route: list[str] = []
    seen_cams = set()
    for lon, lat in sampled:
        for cam in cameras_for({"lat": lat, "lon": lon, "radius_m": EN_ROUTE_RADIUS_M})["cameras"]:
            if cam["camera_id"] not in seen_cams:
                seen_cams.add(cam["camera_id"])
                cameras_en_route.append(cam["camera_id"])

    return {
        "kind": kind,
        "polyline": polyline,
        "length_m": raw["total_m"],
        "segment_ids": [s["segment_id"] for s in segments],
        "cameras_en_route": cameras_en_route,
        "evidence_summary": _evidence_summary(kind, detour_ratio, segments),
        # Non-contract extension (risk_parts, geometry per segment) consumed by
        # map-frontend's evidence sheet until synthesis's SegmentAssessment
        # (SPEC.md §6.4) replaces it. Do not expect other modules to read this.
        "segments": segments,
    }
