"""Static street-context enrichment per camera node — structural facts only.

All from OSM geometry (one alley/crossing Overpass pull, cached + the
arterial ways surukamera already cached). Framing rule (binding): alleys
are presented as structural facts — service roads, fewer exits, no camera
coverage — never "sketchy"; that kind of unexplained verdict is banned.

Per-node `street_context` block:
  sidewalk        "both" | "left" | "right" | "no" | "separate" | null (untagged)
  lit             "yes" | "no" | null            (OSM lit=* on the snapped way)
  highway_class / oneway                          (already on the node)
  camera_gap_m    distance to next camera along the street (node spacing —
                  labeled as camera spacing, not "block length")
  alley_dist_m    distance to the nearest mapped alley centerline point
  crossings_100m  count of mapped pedestrian crossings within 100 m
  source          "osm"

Build: python -m ingest.statics   (writes into camera_graph.json)
"""
from __future__ import annotations

import json
import math
import time

from . import config, netboot
from .graph import CameraGraph, to_xy

ALLEY_CACHE = config.DATA / "osm_alleys.json"


def _overpass(q: str, client) -> dict | None:
    for url in (config.OVERPASS, config.OVERPASS_MIRROR):
        try:
            r = client.post(url, data={"data": q})
            r.raise_for_status()
            return r.json()
        except Exception:
            time.sleep(3)
    return None


def _fetch_alleys_crossings() -> dict:
    """Two smaller pulls (Overpass chokes on the combined one under load).
    Either half may fail — the build degrades to partial rather than dying."""
    if ALLEY_CACHE.exists():
        return json.loads(ALLEY_CACHE.read_text(encoding="utf-8"))
    bbox = "47.48,-122.45,47.75,-122.22"
    client = netboot.make_client(timeout=240.0)
    try:
        alleys = _overpass(
            f'[out:json][timeout:120];way["highway"="service"]["service"="alley"]'
            f"({bbox});out geom;", client)
        crossings = _overpass(
            f'[out:json][timeout:120];node["highway"="crossing"]({bbox});out;',
            client)
    finally:
        client.close()
    payload = {
        "elements": ((alleys or {}).get("elements", [])
                     + (crossings or {}).get("elements", [])),
        "partial": {"alleys": alleys is None, "crossings": crossings is None},
    }
    if alleys is not None or crossings is not None:
        ALLEY_CACHE.write_text(json.dumps(payload), encoding="utf-8")
    return payload


def _sidewalk_of(tags: dict) -> str | None:
    if "sidewalk" in tags:
        return tags["sidewalk"]
    if tags.get("sidewalk:both") in ("yes", "separate"):
        return "both"
    l = tags.get("sidewalk:left") == "yes"
    r = tags.get("sidewalk:right") == "yes"
    if l and r:
        return "both"
    if l:
        return "left"
    if r:
        return "right"
    if tags.get("sidewalk:both") == "no" or (
            tags.get("sidewalk:left") == "no" and tags.get("sidewalk:right") == "no"):
        return "no"
    return None


def build() -> dict:
    g = CameraGraph.load()

    # snapped-way tags from the arterial cache surukamera shipped
    osm = json.loads(config.SURU_DATA.joinpath("osm_ways.json").read_text(encoding="utf-8"))
    way_tags = {w["id"]: w.get("tags", {}) for w in osm["elements"]
                if w["type"] == "way"}

    data = _fetch_alleys_crossings()
    alley_pts: list[tuple[float, float]] = []
    crossing_pts: list[tuple[float, float]] = []
    for el in data.get("elements", []):
        if el["type"] == "way":
            for gpt in el.get("geometry") or []:
                alley_pts.append(to_xy(gpt["lat"], gpt["lon"]))
        elif el["type"] == "node":
            crossing_pts.append(to_xy(el["lat"], el["lon"]))

    # coarse grids for the two point sets
    cell = 250.0

    def grid_of(pts):
        gr: dict[tuple[int, int], list[tuple[float, float]]] = {}
        for x, y in pts:
            gr.setdefault((int(x // cell), int(y // cell)), []).append((x, y))
        return gr

    def min_dist(gr, x, y, max_ring=4):
        cx, cy = int(x // cell), int(y // cell)
        best = None
        for ring in range(max_ring + 1):
            for dx in range(-ring, ring + 1):
                for dy in range(-ring, ring + 1):
                    if max(abs(dx), abs(dy)) != ring:
                        continue
                    for px, py in gr.get((cx + dx, cy + dy), []):
                        d = math.hypot(px - x, py - y)
                        if best is None or d < best:
                            best = d
            if best is not None and best <= ring * cell:
                break   # nothing in a farther ring can beat this
        return best

    alley_grid, crossing_grid = grid_of(alley_pts), grid_of(crossing_pts)

    # camera spacing along each street from the ordered street index
    gap: dict[str, float] = {}
    for name, ids in g.street_index.items():
        for i, cid in enumerate(ids):
            ds = []
            for j in (i - 1, i + 1):
                if 0 <= j < len(ids):
                    a, b = g.nodes[cid], g.nodes[ids[j]]
                    ax, ay = to_xy(a["lat"], a["lon"])
                    bx, by = to_xy(b["lat"], b["lon"])
                    ds.append(math.hypot(ax - bx, ay - by))
            if ds:
                gap[cid] = min(ds)

    partial = data.get("partial", {})
    no_alleys = partial.get("alleys", False)
    no_crossings = partial.get("crossings", False)

    done = 0
    for cid, node in g.nodes.items():
        x, y = to_xy(node["lat"], node["lon"])
        tags = way_tags.get(node.get("osm_way_id"), {})
        ad = None if no_alleys else min_dist(alley_grid, x, y)
        # a failed pull is UNKNOWN, never "zero crossings" — that would be a claim
        crossings = None if no_crossings else sum(
            1 for pts in (crossing_grid.get((int(x // cell) + dx, int(y // cell) + dy), [])
                          for dx in (-1, 0, 1) for dy in (-1, 0, 1))
            for px, py in pts if math.hypot(px - x, py - y) <= 100.0)
        node["street_context"] = {
            "sidewalk": _sidewalk_of(tags),
            "lit": tags.get("lit"),
            "camera_gap_m": round(gap[cid], 0) if cid in gap else None,
            "alley_dist_m": round(ad, 0) if ad is not None else None,
            "crossings_100m": crossings,
            "source": "osm-partial" if (no_alleys or no_crossings) else "osm",
            "computed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        done += 1
    g.save()
    return {"nodes": done, "alley_pts": len(alley_pts),
            "crossings": len(crossing_pts)}


if __name__ == "__main__":
    print(json.dumps(build(), indent=2))
