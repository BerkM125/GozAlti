"""Step 1 — the camera positional artifact.

An adjacency-list graph over every camera worth scraping (servstat ACTV),
each node carrying the full Seattle open-dataset metadata plus surukamera's
street-snap enrichment. Built entirely offline from the shipped artifacts
(experiments/surukamera/data/cameras.json + streets.json).

Edges:
  * "street"    — consecutive cameras along the same named street (from
                  surukamera's PCA along-street ordering)
  * "proximity" — nodes within PROXIMITY_EDGE_M of each other, so BFS can
                  flow between streets and across components

Spatial queries work HNSW-style in spirit — a query point acts like a
virtual node "inserted" next to its neighbors — implemented with a uniform
grid index (cells ~150 m), which at 650 nodes is exact and instant:

    g = CameraGraph.load()
    g.nearby(lat, lon, radius_m=100)  -> nodes sorted by distance
    g.nearest(lat, lon, k=5)
    g.street("Pike Street")           -> ordered nodes
    g.street_near(lat, lon)           -> best street name for a click
    g.convergence(lat, lon, 150)      -> SPEC §6.7 CameraConvergence shape
    g.bfs()                           -> every node, BFS order, all components

Build/rebuild: python -m ingest.graph
"""
from __future__ import annotations

import json
import math
import sys
import time
from collections import deque
from pathlib import Path

from . import config

LAT0, LON0 = 47.61, -122.33
M_PER_DEG_LAT = 110_574.0
M_PER_DEG_LON = 111_320.0 * math.cos(math.radians(LAT0))


def to_xy(lat: float, lon: float) -> tuple[float, float]:
    """Equirectangular meters relative to downtown Seattle."""
    return ((lon - LON0) * M_PER_DEG_LON, (lat - LAT0) * M_PER_DEG_LAT)


def from_xy(x: float, y: float) -> tuple[float, float]:
    return (LAT0 + y / M_PER_DEG_LAT, LON0 + x / M_PER_DEG_LON)


def dist_m(a_lat: float, a_lon: float, b_lat: float, b_lon: float) -> float:
    ax, ay = to_xy(a_lat, a_lon)
    bx, by = to_xy(b_lat, b_lon)
    return math.hypot(ax - bx, ay - by)


def dest_point(lat: float, lon: float, bearing_deg: float, range_m: float) -> tuple[float, float]:
    """Point range_m away from (lat, lon) along a compass bearing."""
    x, y = to_xy(lat, lon)
    rad = math.radians(bearing_deg)
    return from_xy(x + range_m * math.sin(rad), y + range_m * math.cos(rad))


# ------------------------------------------------------------------ build

def build() -> dict:
    """Assemble the artifact from the shipped surukamera data. Offline."""
    manifest = json.loads(config.SURU_CAMERAS.read_text(encoding="utf-8"))
    streets_art = json.loads(config.SURU_STREETS.read_text(encoding="utf-8"))
    snapped: dict[str, dict] = streets_art["cameras"]

    nodes: dict[str, dict] = {}
    for cam in manifest["cameras"]:
        if cam.get("servstat") != "ACTV":
            continue
        cid = cam["camera_id"]
        node = dict(cam)  # every dataset field the manifest carries
        snap = snapped.get(cid)
        if snap:
            for k in ("street_name", "osm_way_id", "highway_class", "oneway",
                      "snap_dist_m", "snap_x", "snap_y", "road_axis_deg",
                      "way_dir_deg"):
                node[k] = snap.get(k)
        else:
            node.setdefault("street_name", None)
        # bearing block — filled by orientation precompute / manual UI.
        # None until resolved: an unresolved direction is reported as
        # unresolved, never guessed silently (SPEC §7.10).
        node["bearing"] = None
        nodes[cid] = node

    # street index restricted to ACTV nodes, order preserved from surukamera
    street_index: dict[str, list[str]] = {}
    for name, ids in streets_art["street_index"].items():
        kept = [i for i in ids if i in nodes]
        if kept:
            street_index[name] = kept
    # single-camera streets still deserve a street->node mapping
    for cid, node in nodes.items():
        sname = node.get("street_name")
        if sname and cid not in set(street_index.get(sname, [])):
            street_index.setdefault(sname, []).append(cid)

    adjacency: dict[str, list[dict]] = {cid: [] for cid in nodes}

    def link(a: str, b: str, kind: str) -> None:
        d = round(dist_m(nodes[a]["lat"], nodes[a]["lon"],
                         nodes[b]["lat"], nodes[b]["lon"]), 1)
        if not any(e["to"] == b and e["kind"] == kind for e in adjacency[a]):
            adjacency[a].append({"to": b, "kind": kind, "dist_m": d})
        if not any(e["to"] == a and e["kind"] == kind for e in adjacency[b]):
            adjacency[b].append({"to": a, "kind": kind, "dist_m": d})

    for name, ids in street_index.items():
        for i in range(len(ids) - 1):
            link(ids[i], ids[i + 1], "street")

    # proximity edges via a coarse grid pass
    cell = config.PROXIMITY_EDGE_M
    buckets: dict[tuple[int, int], list[str]] = {}
    for cid, n in nodes.items():
        x, y = to_xy(n["lat"], n["lon"])
        buckets.setdefault((int(x // cell), int(y // cell)), []).append(cid)
    for (bx, by), ids in buckets.items():
        neigh = []
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                neigh.extend(buckets.get((bx + dx, by + dy), []))
        for a in ids:
            for b in neigh:
                if a >= b:
                    continue
                if dist_m(nodes[a]["lat"], nodes[a]["lon"],
                          nodes[b]["lat"], nodes[b]["lon"]) <= config.PROXIMITY_EDGE_M:
                    link(a, b, "proximity")

    artifact = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "source": {
            "manifest": str(config.SURU_CAMERAS.relative_to(config.REPO_ROOT)),
            "streets": str(config.SURU_STREETS.relative_to(config.REPO_ROOT)),
        },
        "counts": {
            "nodes": len(nodes),
            "with_stream": sum(1 for n in nodes.values() if n.get("has_stream")),
            "street_snapped": sum(1 for n in nodes.values() if n.get("street_name")),
            "streets": len(street_index),
            "edges": sum(len(v) for v in adjacency.values()) // 2,
        },
        "nodes": nodes,
        "adjacency": adjacency,
        "street_index": street_index,
    }
    config.GRAPH_JSON.write_text(json.dumps(artifact, indent=1), encoding="utf-8")
    return artifact


# ------------------------------------------------------------------ queries

class CameraGraph:
    def __init__(self, artifact: dict):
        self.artifact = artifact
        self.nodes: dict[str, dict] = artifact["nodes"]
        self.adjacency: dict[str, list[dict]] = artifact["adjacency"]
        self.street_index: dict[str, list[str]] = artifact["street_index"]
        # grid spatial index, rebuilt on load (cheap at this node count)
        self._cell = config.GRID_CELL_M
        self._grid: dict[tuple[int, int], list[str]] = {}
        self._xy: dict[str, tuple[float, float]] = {}
        for cid, n in self.nodes.items():
            xy = to_xy(n["lat"], n["lon"])
            self._xy[cid] = xy
            key = (int(xy[0] // self._cell), int(xy[1] // self._cell))
            self._grid.setdefault(key, []).append(cid)

    @classmethod
    def load(cls, rebuild: bool = False) -> "CameraGraph":
        if rebuild or not config.GRAPH_JSON.exists():
            return cls(build())
        return cls(json.loads(config.GRAPH_JSON.read_text(encoding="utf-8")))

    def save(self) -> None:
        config.GRAPH_JSON.write_text(
            json.dumps(self.artifact, indent=1), encoding="utf-8")

    # -- spatial ----------------------------------------------------------

    def nearby(self, lat: float, lon: float, radius_m: float = 100.0) -> list[dict]:
        """All nodes within radius_m of the point, nearest first, each with
        a `dist_m` field attached."""
        qx, qy = to_xy(lat, lon)
        ring = int(radius_m // self._cell) + 1
        cx, cy = int(qx // self._cell), int(qy // self._cell)
        hits = []
        for dx in range(-ring, ring + 1):
            for dy in range(-ring, ring + 1):
                for cid in self._grid.get((cx + dx, cy + dy), []):
                    x, y = self._xy[cid]
                    d = math.hypot(x - qx, y - qy)
                    if d <= radius_m:
                        hits.append((d, cid))
        hits.sort()
        return [{**self.nodes[cid], "dist_m": round(d, 1)} for d, cid in hits]

    def nearest(self, lat: float, lon: float, k: int = 5,
                max_radius_m: float = 3000.0) -> list[dict]:
        """k nearest nodes — expand the search radius until enough found."""
        r = 200.0
        while r <= max_radius_m:
            found = self.nearby(lat, lon, r)
            if len(found) >= k:
                return found[:k]
            r *= 2
        return self.nearby(lat, lon, max_radius_m)[:k]

    # -- streets ----------------------------------------------------------

    def streets(self) -> list[dict]:
        return [{"name": n, "cameras": len(ids)}
                for n, ids in sorted(self.street_index.items())]

    def street(self, name: str) -> list[dict]:
        """Cameras on a street, ordered along the street."""
        return [self.nodes[cid] for cid in self.street_index.get(name, [])]

    def street_near(self, lat: float, lon: float,
                    radius_m: float = 150.0) -> str | None:
        """The street a map click most plausibly targets: the street of the
        closest street-snapped node within radius."""
        for n in self.nearby(lat, lon, radius_m):
            if n.get("street_name"):
                return n["street_name"]
        return None

    # -- traversal --------------------------------------------------------

    def bfs(self, start: str | None = None) -> list[str]:
        """BFS order over every node; multiple components handled by
        restarting at the nearest unvisited node."""
        order: list[str] = []
        visited: set[str] = set()
        ids = list(self.nodes)
        queue: deque[str] = deque()
        if start and start in self.nodes:
            queue.append(start)
        for seed in ([start] if start in self.nodes else []) + ids:
            if seed in visited or seed is None:
                continue
            queue.append(seed)
            visited.add(seed)
            while queue:
                cur = queue.popleft()
                order.append(cur)
                for edge in self.adjacency.get(cur, []):
                    nxt = edge["to"]
                    if nxt not in visited:
                        visited.add(nxt)
                        queue.append(nxt)
        return order

    # -- contract shapes --------------------------------------------------

    def _to_convergence_cam(self, n: dict) -> dict:
        from . import activity  # local import: activity has no graph dependency
        b = n.get("bearing") or {}
        return {
            "camera_id": n["camera_id"],
            "lat": n["lat"], "lon": n["lon"],
            "bearing_deg": b.get("bearing_deg"),
            "bearing_conf": b.get("bearing_conf"),
            "live_hls": n.get("hls_url") if n.get("has_stream") else None,
            "snapshot_url": n.get("snapshot_url"),
            "activity": activity.effective_activity(n),
            "last_activity_at": n.get("last_activity_at"),
        }

    def convergence(self, lat: float | None = None, lon: float | None = None,
                    radius_m: float = 300.0, street: str | None = None) -> dict:
        """CameraConvergence (SPEC §6.7) for a point or a street name."""
        if street:
            cams = self.street(street)
            query: dict = {"street": street}
        else:
            cams = self.nearby(lat, lon, radius_m)
            query = {"lat": lat, "lon": lon, "radius_m": radius_m}
        return {"query": query,
                "cameras": [self._to_convergence_cam(c) for c in cams]}


if __name__ == "__main__":
    art = build()
    print(json.dumps(art["counts"], indent=2))
    g = CameraGraph(art)
    # smoke: 4th & Pike downtown
    for n in g.nearby(47.6107, -122.3378, 150):
        print(f"  {n['camera_id']:<10} {n['dist_m']:>6}m  {n['location_desc']}")
