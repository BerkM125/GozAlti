"""Refuge layer — open businesses/buildings to duck into, near a camera or
along a street.

Source: OSM via Overpass, **only elements that carry `opening_hours` + a
name** (one cached city-wide pull, rebuildable). OSM's hours coverage in
Seattle is decent for chains and patchy for small shops, so every number
this module emits is scoped to that: "N businesses *with known hours*,
M open now, nearest open X m" — never a claim of completeness, and a place
whose hours we can't parse counts as "unknown", not closed. No Google
Places (ToS forbids storing fields; OSM is free and legal to cache).

open/closed is evaluated live at query time (America/Los_Angeles) from the
stored hours spec — we never persist an "open" flag that could go stale.

Build/refresh the dataset: python -m ingest.refuge
"""
from __future__ import annotations

import json
import math
import time
from datetime import datetime

from . import config, hours, netboot
from .graph import to_xy

# refuge-relevant POI classes (staffed, enterable places).
# "office" is pulled but filtered at load: an office tower tagged 24/7 is
# not a place a pedestrian can walk into.
_KIND_KEYS = ("amenity", "shop", "leisure", "tourism", "healthcare", "office")
_EXCLUDE_KIND_PREFIXES = ("office=",)

_GRID_CELL = 200.0
_pois: list[dict] | None = None
_grid: dict[tuple[int, int], list[int]] = {}


def build(bbox: tuple[float, float, float, float] | None = None) -> dict:
    """One Overpass pull: named elements with opening_hours in the camera
    bbox. Cached to data/refuge_pois.json."""
    if bbox is None:
        bbox = (47.48, -122.45, 47.75, -122.22)   # covers every camera
    q = f"""
[out:json][timeout:180];
(
  node["name"]["opening_hours"]({bbox[0]},{bbox[1]},{bbox[2]},{bbox[3]});
  way["name"]["opening_hours"]({bbox[0]},{bbox[1]},{bbox[2]},{bbox[3]});
);
out center tags;
"""
    client = netboot.make_client(timeout=240.0)
    r = client.post(config.OVERPASS, data={"data": q})
    r.raise_for_status()
    payload = r.json()
    client.close()

    pois = []
    for el in payload.get("elements", []):
        tags = el.get("tags", {})
        lat = el.get("lat") or (el.get("center") or {}).get("lat")
        lon = el.get("lon") or (el.get("center") or {}).get("lon")
        if lat is None or lon is None:
            continue
        kind = next((f"{k}={tags[k]}" for k in _KIND_KEYS if k in tags), None)
        if kind is None:
            continue   # named+hours but not an enterable-place class
        pois.append({
            "name": tags["name"],
            "kind": kind,
            "lat": lat, "lon": lon,
            "opening_hours": tags["opening_hours"],
            "osm_id": f"{el['type']}/{el['id']}",
        })
    out = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "source": "osm-overpass",
        "count": len(pois),
        "pois": pois,
    }
    (config.DATA / "refuge_pois.json").write_text(
        json.dumps(out, indent=1), encoding="utf-8")
    _load(force=True)
    return out


def _load(force: bool = False) -> bool:
    global _pois
    if _pois is not None and not force:
        return True
    p = config.DATA / "refuge_pois.json"
    if not p.exists():
        return False
    data = json.loads(p.read_text(encoding="utf-8"))
    _pois = [poi for poi in data["pois"]
             if not poi["kind"].startswith(_EXCLUDE_KIND_PREFIXES)]
    _grid.clear()
    for i, poi in enumerate(_pois):
        x, y = to_xy(poi["lat"], poi["lon"])
        poi["_x"], poi["_y"] = x, y
        _grid.setdefault((int(x // _GRID_CELL), int(y // _GRID_CELL)), []).append(i)
    return True


def available() -> bool:
    return _load()


def _evaluated(poi: dict, at: datetime | None) -> dict:
    open_now, open_until = hours.evaluate(poi["opening_hours"], at)
    return {
        "name": poi["name"], "kind": poi["kind"],
        "lat": poi["lat"], "lon": poi["lon"],
        "opening_hours": poi["opening_hours"],
        "open_now": open_now,          # true | false | null (couldn't parse)
        "open_until": open_until,
        "osm_id": poi["osm_id"],
    }


def near(lat: float, lon: float, radius_m: float = 150.0,
         at: datetime | None = None) -> dict:
    """Refuge summary for a point. Honest scope: counts cover only places
    with an OSM opening_hours tag."""
    if not _load():
        return {"available": False,
                "why": "refuge dataset not built — run python -m ingest.refuge"}
    qx, qy = to_xy(lat, lon)
    ring = int(radius_m // _GRID_CELL) + 1
    cx, cy = int(qx // _GRID_CELL), int(qy // _GRID_CELL)
    found = []
    for dx in range(-ring, ring + 1):
        for dy in range(-ring, ring + 1):
            for i in _grid.get((cx + dx, cy + dy), []):
                poi = _pois[i]
                d = math.hypot(poi["_x"] - qx, poi["_y"] - qy)
                if d <= radius_m:
                    found.append((d, poi))
    found.sort(key=lambda t: t[0])
    evaluated = [{**_evaluated(p, at), "dist_m": round(d, 1)} for d, p in found]
    open_pois = [p for p in evaluated if p["open_now"] is True]
    return {
        "available": True,
        "basis": "osm-opening-hours",   # what this is and nothing more
        "radius_m": radius_m,
        "n_known_hours": len(evaluated),
        "n_open_now": len(open_pois),
        "n_hours_unparsed": sum(1 for p in evaluated if p["open_now"] is None),
        "nearest_open": open_pois[0] if open_pois else None,
        "pois": evaluated[:25],
    }


def in_bbox(s: float, w: float, n: float, e: float,
            at: datetime | None = None, limit: int = 400) -> list[dict]:
    """Evaluated POIs inside a bbox — the UI's map layer."""
    if not _load():
        return []
    out = []
    for poi in _pois:
        if s <= poi["lat"] <= n and w <= poi["lon"] <= e:
            out.append(_evaluated(poi, at))
            if len(out) >= limit:
                break
    return out


def along_street(nodes: list[dict], radius_m: float = 120.0,
                 at: datetime | None = None) -> dict:
    """Aggregate refuge summary along a street: union of each camera's
    vicinity, deduped by osm_id."""
    seen: dict[str, dict] = {}
    for node in nodes:
        res = near(node["lat"], node["lon"], radius_m, at)
        if not res.get("available"):
            return res
        for p in res["pois"]:
            prev = seen.get(p["osm_id"])
            if prev is None or p["dist_m"] < prev["dist_m"]:
                seen[p["osm_id"]] = p
    pois = sorted(seen.values(), key=lambda p: p["dist_m"])
    open_pois = [p for p in pois if p["open_now"] is True]
    return {
        "available": True,
        "basis": "osm-opening-hours",
        "cameras_considered": len(nodes),
        "radius_m": radius_m,
        "n_known_hours": len(pois),
        "n_open_now": len(open_pois),
        "n_hours_unparsed": sum(1 for p in pois if p["open_now"] is None),
        "nearest_open": open_pois[0] if open_pois else None,
        "pois": pois[:40],
    }


if __name__ == "__main__":
    res = build()
    print(f"refuge dataset: {res['count']} named POIs with opening_hours")
    demo = near(47.6107, -122.3378, 150)   # 4th & Pine
    print(json.dumps({k: v for k, v in demo.items() if k != "pois"}, indent=1))
