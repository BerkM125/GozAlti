"""Evidence-enriched pathfinding: harness A* + everything this module knows.

The route itself comes from Ioli's harness (modules/harness, imported
read-only): risk-weighted A* over the real walk graph — risk is native to
the search ("safer" multiplies edge cost by structural risk), with
per-segment base_risk + risk_parts from OSM structure. This module then
overlays its LIVE evidence on each segment:

  camera coverage     graph cameras within 80 m of the segment midpoint
  watched             any of them pixel-active right now (activity flag)
  last person seen    copresence recency across those cameras (minutes)
  lighting            street_context `lit` tag + whether the sun is up (NOAA)
  open refuge         businesses open RIGHT NOW within 120 m (OSM hours)
  sidewalk/alley      street_context structural facts

and combines base_risk with those into `live_risk` via the documented
formula below — a transparent, deterministic evidence combination for the
demo, NOT a synthesis verdict; every input rides along in `evidence` so any
number can be checked. The harness segments' `live_penalty`/`confidence`/
`stale` fields are deterministic jitter placeholders (their code says so) —
they are dropped here, never forwarded as real.

RISK_FORMULA (each term also present in `evidence`):
  live_risk = clamp01( base_risk
      + 0.15 if night AND way not tagged lit
      + 0.10 if zero cameras cover the segment
      - 0.05 if covered, further -0.05 if any camera is pixel-active
      + 0.10 if zero businesses open within 120 m
      - 0.10 * min(open_count, 3)/3 otherwise )
  buckets: low < 0.35 <= medium < 0.65 <= high
"""
from __future__ import annotations

import calendar
import sys
import time

from . import activity, config, refuge, solar

sys.path.insert(0, str(config.REPO_ROOT / "modules" / "harness"))
import harness  # noqa: E402  (Ioli's module, read-only)

RISK_FORMULA = ("base_risk (harness structural A* weights) "
                "+0.15 night&unlit +0.10 no-camera -0.05 covered "
                "-0.05 pixel-active +0.10 no-open-refuge "
                "-0.10*min(open,3)/3; clamp [0,1]; "
                "low<0.35<=medium<0.65<=high — mechanical evidence "
                "combination for the demo, not a synthesis verdict")


def _bucket(r: float) -> str:
    return "low" if r < 0.35 else "medium" if r < 0.65 else "high"


def _minutes_since(iso: str | None) -> float | None:
    if not iso:
        return None
    try:
        t = calendar.timegm(time.strptime(iso, "%Y-%m-%dT%H:%M:%SZ"))
        return round((time.time() - t) / 60, 1)
    except ValueError:
        return None


def _enrich_segment(g, seg: dict, daylight: bool) -> None:
    coords = seg["geometry"]["coordinates"]
    mid_lon, mid_lat = coords[len(coords) // 2]

    cams = g.nearby(mid_lat, mid_lon, 80.0)
    acts = [activity.effective_activity(g.nodes[c["camera_id"]]) for c in cams]
    active_n = sum(1 for a in acts if a and a.get("active") is True)

    person_mins = []
    lit = sidewalk = None
    alley_dist = None
    for c in cams:
        node = g.nodes[c["camera_id"]]
        cop = node.get("copresence")
        if cop:
            m = _minutes_since(cop.get("last_person_at"))
            if m is not None:
                person_mins.append(m)
        sc = node.get("street_context")
        if sc and lit is None:
            lit, sidewalk = sc.get("lit"), sc.get("sidewalk")
            alley_dist = sc.get("alley_dist_m")

    ref = refuge.near(mid_lat, mid_lon, 120.0)
    open_n = ref.get("n_open_now", 0) if ref.get("available") else 0
    nearest_open = ref.get("nearest_open") if ref.get("available") else None

    risk = seg["base_risk"]
    unlit_night = (not daylight) and lit != "yes"
    if unlit_night:
        risk += 0.15
    if not cams:
        risk += 0.10
    else:
        risk -= 0.05
        if active_n:
            risk -= 0.05
    if open_n:
        risk -= 0.10 * min(open_n, 3) / 3
    else:
        risk += 0.10
    risk = max(0.0, min(1.0, risk))

    seg["live_risk"] = round(risk, 2)
    seg["risk_bucket"] = _bucket(risk)
    seg["evidence"] = {
        "cameras_80m": [c["camera_id"] for c in cams],
        "cameras_active": active_n,
        "last_person_min": min(person_mins) if person_mins else None,
        "lit": lit, "sidewalk": sidewalk, "alley_dist_m": alley_dist,
        "daylight": daylight, "night_unlit_penalty": unlit_night,
        "open_refuges_120m": open_n,
        "nearest_open": ({"name": nearest_open["name"],
                          "dist_m": nearest_open["dist_m"],
                          "open_until": nearest_open.get("open_until")}
                         if nearest_open else None),
    }
    # deterministic-jitter placeholders from harness — not real, not forwarded
    for k in ("live_penalty", "confidence", "stale"):
        seg.pop(k, None)


def route_enriched(g, olat: float, olon: float, dlat: float, dlon: float,
                   kind: str = "safer") -> dict:
    """Two-click pathfinding for the demo UI. Raises harness.RouteError."""
    r = harness.route((olon, olat), (dlon, dlat), kind)

    _, el = solar.solar_position(olat, olon, time.time())
    daylight = el > 0

    for seg in r.get("segments", []):
        _enrich_segment(g, seg, daylight)

    # cameras that would see the walk, with this module's live state attached
    cams = []
    for cid in r["cameras_en_route"]:
        node = g.nodes.get(cid)
        if not node:
            continue
        act = activity.effective_activity(node)
        cams.append({
            "camera_id": cid, "lat": node["lat"], "lon": node["lon"],
            "location_desc": node.get("location_desc"),
            "street": node.get("street_name"),
            "has_stream": bool(node.get("has_stream")),
            "active": act.get("active") if act else None,
            "last_person_at": (node.get("copresence") or {}).get("last_person_at"),
        })

    # open "exit routes" along the walk: businesses open right now near the
    # path, deduped, nearest-first (OSM known-hours scope, as always)
    exits: dict[str, dict] = {}
    pl = r["polyline"]
    for lat, lon in pl[:: max(1, len(pl) // 20)] + [pl[-1]]:
        res = refuge.near(lat, lon, 60.0)
        if not res.get("available"):
            break
        for p in res["pois"]:
            if p["open_now"] is not True:
                continue
            prev = exits.get(p["osm_id"])
            if prev is None or p["dist_m"] < prev["dist_m"]:
                exits[p["osm_id"]] = p
    r["refuges_en_route"] = sorted(exits.values(), key=lambda p: p["dist_m"])[:30]

    r["cameras_en_route_detail"] = cams
    r["daylight"] = daylight
    r["risk_basis"] = RISK_FORMULA
    r["eta_min"] = round(r["length_m"] / 80.0, 1)
    return r
