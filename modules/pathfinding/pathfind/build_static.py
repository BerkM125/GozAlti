"""Per-edge static overlay: camera coverage, REAL SDOT collisions, osint.

    python -m pathfind.build_static            # full build
    build(skip_sdot=True)                      # instant build, collisions
                                               # pending (live.py backfills)

Outputs data/edge_static.json:
    edges: [ {coverage, collisions, osint, cams:[camera_id,...]} x n_edges ]
    meta:  sources + layers_pending

Sources, honestly labeled:
  coverage    modules/media-ingest camera_graph.json (646 cameras) — gap to
              nearest camera + density within 100 m. More coverage = safer.
  collisions  SDOT_Collisions_All_Years_1 via Seattle's ArcGIS (safe-walk's
              exact service + field list, ported) — pedestrian+cyclist
              density per 100 m, capped like safe-walk. Fetch fails/skipped
              -> zeros + "sdot-collisions" in layers_pending. NEVER faked.
  osint       modules/osint data/signals.latest.json (§6.3 AreaSignals):
              (1 - sentiment)/2 x confidence for areas within 400 m of the
              edge. Absent (Dhruv hasn't produced data yet) -> zeros +
              "osint-signals" pending. Re-run this builder when it lands.
"""
from __future__ import annotations

import json
import math
import sys
import time
from pathlib import Path

from .core import DATA, MODULE_ROOT, REPO, camera_graph, graph, to_xy

ARCGIS = ("https://services.arcgis.com/ZOyb2t4B0UYuYNYH/ArcGIS/rest/services/"
          "SDOT_Collisions_All_Years_1/FeatureServer/0/query")
BBOX = (47.595, -122.355, 47.672, -122.285)      # walk-graph bbox (downtown + U-District/UW, 16 Aug)
SDOT_CACHE = DATA / "sdot_collisions.json"
PED_MAXED_PER100 = 2.5                           # safe-walk's saturation


def _fetch_sdot() -> list[dict] | None:
    """Pull ped/cyclist collisions for the bbox; cached forever after."""
    if SDOT_CACHE.exists():
        return json.loads(SDOT_CACHE.read_text())
    sys.path.insert(0, str(REPO / "modules" / "media-ingest"))
    from ingest import netboot
    params = {
        "where": "PEDCOUNT>0 OR PEDCYLCOUNT>0",
        "geometry": json.dumps({"xmin": BBOX[1], "ymin": BBOX[0],
                                "xmax": BBOX[3], "ymax": BBOX[2],
                                "spatialReference": {"wkid": 4326}}),
        "geometryType": "esriGeometryEnvelope", "inSR": 4326,
        "spatialRel": "esriSpatialRelIntersects",
        "outFields": "PEDCOUNT,PEDCYLCOUNT,SERIOUSINJURIES,FATALITIES",
        "returnGeometry": "true", "outSR": 4326, "f": "json",
        "resultRecordCount": 4000,
    }
    client = netboot.make_client(timeout=120.0)
    feats = []
    try:
        offset = 0
        while True:                       # ArcGIS pages at 2000 records
            r = client.get(ARCGIS, params={**params, "resultOffset": offset})
            r.raise_for_status()
            page = r.json()
            feats.extend(page.get("features", []))
            if not page.get("exceededTransferLimit"):
                break
            offset = len(feats)
    except Exception:
        if not feats:
            return None                   # partial page set is still real data
    finally:
        client.close()
    pts = [{"lat": f["geometry"]["y"], "lon": f["geometry"]["x"],
            "ped": f["attributes"].get("PEDCOUNT") or 0,
            "cyl": f["attributes"].get("PEDCYLCOUNT") or 0,
            "serious": (f["attributes"].get("SERIOUSINJURIES") or 0)
                       + (f["attributes"].get("FATALITIES") or 0)}
           for f in feats if f.get("geometry")]
    SDOT_CACHE.write_text(json.dumps(pts))
    return pts


def _osint_areas() -> list[dict]:
    p = REPO / "modules" / "osint" / "data" / "signals.latest.json"
    if not p.exists():
        return []
    try:
        sigs = json.loads(p.read_text())
        if isinstance(sigs, dict):
            sigs = sigs.get("signals", [])
        out = []
        for s in sigs:
            lat = s.get("lat") or (s.get("centroid") or {}).get("lat")
            lon = s.get("lon") or (s.get("centroid") or {}).get("lon")
            if lat is None:
                continue
            out.append({"lat": lat, "lon": lon,
                        "risk": max(0.0, (1 - s.get("sentiment", 0)) / 2
                                    * s.get("confidence", 0.5))})
        return out
    except Exception:
        return []


def build(skip_sdot: bool = False) -> dict:
    g = graph()
    cg = camera_graph()
    t0 = time.monotonic()
    pending = []

    cam_xy = [(to_xy(n["lat"], n["lon"]), cid) for cid, n in cg.nodes.items()]
    cell = {}
    for (x, y), cid in cam_xy:
        cell.setdefault((int(x // 200), int(y // 200)), []).append((x, y, cid))

    sdot = None if skip_sdot else _fetch_sdot()
    if sdot is None:
        pending.append("sdot-collisions")
        sgrid = {}
    else:
        sgrid = {}
        for p in sdot:
            x, y = to_xy(p["lat"], p["lon"])
            sgrid.setdefault((int(x // 200), int(y // 200))
                             , []).append((x, y, p["ped"] + p["cyl"]))

    osint = _osint_areas()
    if not osint:
        pending.append("osint-signals")
    ogrid = [(to_xy(a["lat"], a["lon"]), a["risk"]) for a in osint]

    edges_out = []
    for a, b, length, wi in g.edges:
        mx = (g.xy[a][0] + g.xy[b][0]) / 2
        my = (g.xy[a][1] + g.xy[b][1]) / 2
        # coverage: gap to nearest camera + density within 100 m
        near, cams = 1e9, []
        cx, cy = int(mx // 200), int(my // 200)
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for x, y, cid in cell.get((cx + dx, cy + dy), []):
                    d = math.hypot(x - mx, y - my)
                    near = min(near, d)
                    if d <= 100.0:
                        cams.append(cid)
        gap = 1.0 if near > 120 else 0.0 if near <= 40 else (near - 40) / 80.0
        coverage = max(0.0, gap - 0.15 * max(0, len(cams) - 1))
        # collisions: ped+cyl count within 60 m, per 100 m of edge
        col = 0.0
        if sgrid:
            hits = 0
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    for x, y, n_pc in sgrid.get((cx + dx, cy + dy), []):
                        if math.hypot(x - mx, y - my) <= 60.0:
                            hits += n_pc
            col = min(1.0, (hits / max(length / 100.0, 0.6)) / PED_MAXED_PER100)
        # osint: nearest signal within 400 m
        osr = 0.0
        for (x, y), risk in ogrid:
            if math.hypot(x - mx, y - my) <= 400.0:
                osr = max(osr, risk)
        edges_out.append({"coverage": round(coverage, 3),
                          "collisions": round(col, 3),
                          "osint": round(osr, 3),
                          "cams": cams})

    meta = {"built_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "layers_pending": pending,
            "sources": {"coverage": "media-ingest camera_graph (646 cams)",
                        "collisions": ("sdot-arcgis SDOT_Collisions_All_Years_1"
                                       if sdot is not None else "PENDING"),
                        "osint": ("signals.latest.json" if osint else "PENDING"),
                        "n_sdot_points": len(sdot) if sdot else 0}}
    (DATA / "edge_static.json").write_text(
        json.dumps({"edges": edges_out, "meta": meta}))
    # hot-reload into the live singleton
    g.static, g.static_meta = edges_out, meta
    return {"edges": len(edges_out), "pending": pending,
            "sdot_points": len(sdot) if sdot else 0,
            "osint_areas": len(osint),
            "took_s": round(time.monotonic() - t0, 1)}


if __name__ == "__main__":
    print(json.dumps(build(), indent=2))
