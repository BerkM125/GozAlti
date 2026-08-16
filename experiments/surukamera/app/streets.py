"""M1: street association.

Pulls the Seattle arterial network from Overpass, snaps each camera to the
nearest named road edge within SNAP_MAX_M, builds street_index
(street name -> cameras ordered along the street), and computes candidate
pairs 60-500 m apart.

Run: python -m app.streets
"""
from __future__ import annotations

import json
import math
import time
from pathlib import Path

import numpy as np

from app import netboot

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"

OVERPASS = "https://overpass-api.de/api/interpreter"
BBOX = (47.48, -122.45, 47.75, -122.22)  # covers every camera in the manifest
HIGHWAY_RE = (
    "^(motorway|trunk|primary|secondary|tertiary"
    "|motorway_link|trunk_link|primary_link|secondary_link|tertiary_link"
    "|unclassified|residential)$"
)
SNAP_MAX_M = 40.0
PAIR_MIN_M = 60.0
PAIR_MAX_M = 500.0

LAT0, LON0 = 47.61, -122.33
M_PER_DEG_LAT = 110_574.0
M_PER_DEG_LON = 111_320.0 * math.cos(math.radians(LAT0))


def to_xy(lat: float, lon: float) -> tuple[float, float]:
    return ((lon - LON0) * M_PER_DEG_LON, (lat - LAT0) * M_PER_DEG_LAT)


def fetch_osm() -> dict:
    cache = DATA / "osm_ways.json"
    if cache.exists():
        return json.loads(cache.read_text(encoding="utf-8"))
    query = f"""
[out:json][timeout:180];
way["highway"~"{HIGHWAY_RE}"]["name"]({BBOX[0]},{BBOX[1]},{BBOX[2]},{BBOX[3]});
out geom;
"""
    client = netboot.make_client(timeout=240.0)
    r = client.post(OVERPASS, data={"data": query})
    r.raise_for_status()
    payload = r.json()
    cache.write_text(json.dumps(payload), encoding="utf-8")
    return payload


def normalize_name(name: str) -> str:
    return " ".join(name.split()).strip()


def build() -> dict:
    manifest = json.loads((DATA / "cameras.json").read_text(encoding="utf-8"))
    cameras = [c for c in manifest["cameras"] if c.get("servstat") == "ACTV"]

    osm = fetch_osm()
    ways = [e for e in osm["elements"] if e["type"] == "way" and e.get("geometry")]
    print(f"OSM ways: {len(ways)}")

    # Flatten every way into segments with numpy arrays for fast snapping.
    seg_a, seg_b, seg_way = [], [], []
    way_meta = {}
    for w in ways:
        name = normalize_name(w["tags"]["name"])
        way_meta[w["id"]] = {
            "name": name,
            "highway": w["tags"].get("highway"),
            "oneway": w["tags"].get("oneway", "no"),
        }
        pts = [to_xy(g["lat"], g["lon"]) for g in w["geometry"]]
        for i in range(len(pts) - 1):
            seg_a.append(pts[i])
            seg_b.append(pts[i + 1])
            seg_way.append(w["id"])
    A = np.array(seg_a)
    B = np.array(seg_b)
    WID = np.array(seg_way)
    AB = B - A
    AB_len2 = np.maximum((AB ** 2).sum(axis=1), 1e-9)
    print(f"segments: {len(A)}")

    snapped = []
    for cam in cameras:
        p = np.array(to_xy(cam["lat"], cam["lon"]))
        t = np.clip(((p - A) * AB).sum(axis=1) / AB_len2, 0.0, 1.0)
        proj = A + t[:, None] * AB
        d2 = ((proj - p) ** 2).sum(axis=1)
        idx = int(np.argmin(d2))
        dist = float(math.sqrt(d2[idx]))
        if dist > SNAP_MAX_M:
            continue
        wid = int(WID[idx])
        seg_vec = AB[idx]
        # Directed bearing along the way's node order (compass, 0-360)
        way_dir = math.degrees(math.atan2(seg_vec[0], seg_vec[1])) % 360.0
        snapped.append({
            **cam,
            "street_name": way_meta[wid]["name"],
            "osm_way_id": wid,
            "highway_class": way_meta[wid]["highway"],
            "oneway": way_meta[wid]["oneway"],
            "snap_dist_m": round(dist, 1),
            "snap_x": float(proj[idx][0]),
            "snap_y": float(proj[idx][1]),
            "road_axis_deg": round(way_dir % 180.0, 1),
            "way_dir_deg": round(way_dir, 1),
        })

    # Street index: name -> cameras ordered along the street's principal axis.
    by_street: dict[str, list[dict]] = {}
    for cam in snapped:
        by_street.setdefault(cam["street_name"], []).append(cam)

    streets = {}
    pairs = []
    for name, cams in sorted(by_street.items()):
        if len(cams) < 2:
            continue
        pts = np.array([[c["snap_x"], c["snap_y"]] for c in cams])
        center = pts.mean(axis=0)
        # PCA principal axis for along-street ordering.
        u, s, vt = np.linalg.svd(pts - center)
        axis = vt[0]
        order = np.argsort((pts - center) @ axis)
        ordered = [cams[i] for i in order]
        streets[name] = [c["camera_id"] for c in ordered]
        for i in range(len(ordered) - 1):
            a, b = ordered[i], ordered[i + 1]
            d = math.dist((a["snap_x"], a["snap_y"]), (b["snap_x"], b["snap_y"]))
            if PAIR_MIN_M <= d <= PAIR_MAX_M:
                pairs.append({
                    "street": name,
                    "a": a["camera_id"],
                    "b": b["camera_id"],
                    "gap_m": round(d, 0),
                })

    out = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "counts": {
            "snapped": len(snapped),
            "streets_with_2plus": len(streets),
            "candidate_pairs": len(pairs),
        },
        "cameras": {c["camera_id"]: c for c in snapped},
        "street_index": streets,
        "pairs": pairs,
    }
    (DATA / "streets.json").write_text(json.dumps(out, indent=1), encoding="utf-8")
    return out


if __name__ == "__main__":
    res = build()
    print(json.dumps(res["counts"], indent=2))
    rows = sorted(
        ((name, len(ids)) for name, ids in res["street_index"].items()),
        key=lambda r: -r[1],
    )
    print(f"\n{'street':<38} cameras")
    for name, n in rows[:25]:
        print(f"{name:<38} {n}")
