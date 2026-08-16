#!/usr/bin/env python3
"""Build the offline pedestrian graph for the downtown Seattle bbox.

Fetches the walkable street network from Overpass, keeps the largest connected
component and writes a compact graph harness/routing.py loads at startup. Stdlib only.

    python3 scripts/build-graph.py

Re-runs reuse the cached Overpass response; delete data/osm_raw.json to refetch.
Keep BBOX in sync with modules/map-frontend/src/config.ts.
"""

import json
import math
import pathlib
import sys
import urllib.parse
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
RAW = DATA / "osm_raw.json"
OUT = DATA / "walk_graph.json"

# west, south, east, north — keep map-frontend's src/config.ts BBOX in sync
# Expanded 16 Aug (Berkan, pathfinding sprint — pending Ioli sign-off): union
# rectangle covering the original downtown box PLUS U-District/UW/U-Village
# and the Eastlake/Montlake corridors between, so downtown<->UW routes work.
# One contiguous rectangle is required: the build keeps only the largest
# connected component. Downtown-only was (-122.355, 47.595, -122.315, 47.625).
BBOX = (-122.355, 47.595, -122.285, 47.672)

OVERPASS = "https://overpass-api.de/api/interpreter"

# Ways a pedestrian may use. Everything else is dropped.
WALKABLE = {
    "footway", "path", "pedestrian", "steps", "living_street", "residential",
    "unclassified", "service", "tertiary", "tertiary_link", "secondary",
    "secondary_link", "primary", "primary_link", "track", "road", "cycleway",
}


def fetch() -> dict:
    if RAW.exists():
        print(f"using cached {RAW.relative_to(ROOT)}")
        return json.loads(RAW.read_text())

    w, s, e, n = BBOX
    query = f"""
    [out:json][timeout:180];
    way["highway"]({s},{w},{n},{e});
    (._;>;);
    out body qt;
    """
    print("querying Overpass (this takes a moment)...")
    req = urllib.request.Request(
        OVERPASS,
        data=urllib.parse.urlencode({"data": query}).encode(),
        headers={"User-Agent": "gozalti-harness/0.1 (local dev)"},
    )
    with urllib.request.urlopen(req, timeout=300) as r:
        raw = json.loads(r.read().decode())
    DATA.mkdir(parents=True, exist_ok=True)
    RAW.write_text(json.dumps(raw))
    print(f"cached {len(raw.get('elements', []))} elements -> {RAW.relative_to(ROOT)}")
    return raw


def haversine(a: tuple[float, float], b: tuple[float, float]) -> float:
    R = 6371000.0
    p1, p2 = math.radians(a[1]), math.radians(b[1])
    dp = p2 - p1
    dl = math.radians(b[0] - a[0])
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(math.sqrt(h))


def walkable(tags: dict) -> bool:
    hw = tags.get("highway")
    if hw not in WALKABLE:
        return False
    if tags.get("foot") == "no":
        return False
    if tags.get("access") in ("private", "no"):
        return False
    return True


def build(raw: dict) -> dict:
    coords: dict[int, tuple[float, float]] = {}
    ways = []
    for el in raw.get("elements", []):
        if el["type"] == "node":
            coords[el["id"]] = (el["lon"], el["lat"])
        elif el["type"] == "way" and walkable(el.get("tags", {})):
            ways.append(el)
    print(f"{len(coords)} nodes, {len(ways)} walkable ways")

    # Assign dense internal ids only to nodes actually used by kept ways.
    idx: dict[int, int] = {}
    nodes: list[tuple[float, float]] = []

    def nid(osm_id: int) -> int:
        if osm_id not in idx:
            idx[osm_id] = len(nodes)
            nodes.append(coords[osm_id])
        return idx[osm_id]

    meta = []
    edges: list[tuple[int, int, float, int]] = []
    for w in ways:
        tags = w.get("tags", {})
        refs = [r for r in w["nodes"] if r in coords]
        if len(refs) < 2:
            continue
        wi = len(meta)
        meta.append({
            "name": tags.get("name") or tags.get("ref") or "",
            "highway": tags.get("highway", ""),
            "lit": tags.get("lit", ""),
            "sidewalk": tags.get("sidewalk", ""),
            "id": w["id"],
        })
        for u, v in zip(refs, refs[1:]):
            a, b = nid(u), nid(v)
            if a == b:
                continue
            edges.append((a, b, haversine(coords[u], coords[v]), wi))

    # Largest connected component: a router that can strand you is worse than none.
    adj: list[list[int]] = [[] for _ in nodes]
    for i, (a, b, _, _) in enumerate(edges):
        adj[a].append(i)
        adj[b].append(i)

    seen = [False] * len(nodes)
    best: list[int] = []
    for start in range(len(nodes)):
        if seen[start]:
            continue
        comp, stack = [], [start]
        seen[start] = True
        while stack:
            u = stack.pop()
            comp.append(u)
            for ei in adj[u]:
                a, b, _, _ = edges[ei]
                other = b if a == u else a
                if not seen[other]:
                    seen[other] = True
                    stack.append(other)
        if len(comp) > len(best):
            best = comp

    keep = set(best)
    remap = {old: i for i, old in enumerate(sorted(keep))}
    print(f"largest component: {len(keep)}/{len(nodes)} nodes")

    out_nodes = [[round(nodes[o][0], 7), round(nodes[o][1], 7)] for o in sorted(keep)]
    out_edges = []
    for a, b, d, wi in edges:
        if a in keep and b in keep:
            out_edges.append([remap[a], remap[b], round(d, 2), wi])

    out_adj: list[list[int]] = [[] for _ in out_nodes]
    for i, (a, b, _, _) in enumerate(out_edges):
        out_adj[a].append(i)
        out_adj[b].append(i)

    return {"bbox": list(BBOX), "nodes": out_nodes, "edges": out_edges,
            "adj": out_adj, "ways": meta}


def main() -> int:
    graph = build(fetch())
    DATA.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(graph, separators=(",", ":")))
    mb = OUT.stat().st_size / 1e6
    print(f"wrote {OUT.relative_to(ROOT)}: "
          f"{len(graph['nodes'])} nodes, {len(graph['edges'])} edges, {mb:.1f} MB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
