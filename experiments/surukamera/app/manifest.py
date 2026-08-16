"""M0: build data/cameras.json — the manifest of every reachable camera.

Sources:
  * ArcGIS layer (canonical inventory, 658 records):
      https://services.arcgis.com/ZOyb2t4B0UYuYNYH/arcgis/rest/services/
      Traffic_Cameras_CDL/FeatureServer/0/query
  * Traveler map inventory (join key: Id == ArcGIS UNITID):
      https://web.seattle.gov/Travelers/api/Map/GetCamerasByNeighborhood
  * Wowza HLS template (from /Travelers/api/Map/WowsaUrl, works bare):
      https://61e0c5d388c2e.streamlock.net:443/live/{stream}/playlist.m3u8
    where {stream} = ImageUrl with '.jpg' -> '.stream' (SDOT only).

Run: python -m app.manifest [--probe]
"""
from __future__ import annotations

import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from app import netboot

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"

ARCGIS_QUERY = (
    "https://services.arcgis.com/ZOyb2t4B0UYuYNYH/arcgis/rest/services/"
    "Traffic_Cameras_CDL/FeatureServer/0/query"
    "?where=1%3D1&outFields=*&outSR=4326&f=geojson"
)
TRAVELERS_API = "https://web.seattle.gov/Travelers/api/Map"
NEIGHBORHOODS = [
    "Ballard", "Central", "Delridge", "Downtown", "East", "Greater Duwamish",
    "Lake Union", "Magnolia/Queen Anne", "North", "Northeast", "Northwest",
    "Southeast", "Southwest",
]
HLS_TEMPLATE = "https://61e0c5d388c2e.streamlock.net:443/live/{stream}/playlist.m3u8"
SNAPSHOT_HOSTS = {
    "SDOT": "https://www.seattle.gov/trafficcams/images/",
    "WSDOT": "https://images.wsdot.wa.gov/nw/",
}


def fetch_sources(client) -> tuple[dict, dict]:
    arcgis = client.get(ARCGIS_QUERY).json()
    travelers: dict[str, dict] = {}
    for hood in NEIGHBORHOODS:
        resp = client.get(
            f"{TRAVELERS_API}/GetCamerasByNeighborhood", params={"neighborhood": hood}
        )
        payload = resp.json()
        if isinstance(payload, str):  # API double-encodes JSON
            payload = json.loads(payload)
        for cam in payload:
            cam["Neighborhood"] = hood
            travelers[cam["Id"]] = cam
        time.sleep(0.3)
    return arcgis, travelers


def build(probe: bool = True) -> dict:
    client = netboot.make_client()
    arcgis, travelers = fetch_sources(client)

    cameras = []
    for feat in arcgis["features"]:
        p = feat["properties"]
        lon, lat = feat["geometry"]["coordinates"][:2]
        name = (p.get("NAME") or "").strip()
        unitid = (p.get("UNITID") or "").strip()
        ownership = p.get("OWNERSHIP") or "UNKNOWN"
        url = p.get("URL") or ""
        if not name or not url:
            continue
        key = name[:-4] if name.lower().endswith(".jpg") else name
        snapshot_url = url.replace("http://", "https://")
        tcam = travelers.get(unitid)
        stream_key = f"{key}.stream" if ownership == "SDOT" else None
        cameras.append({
            "camera_id": unitid or key,
            "key": key,
            "ownership": ownership,
            "location_desc": p.get("LOCATION") or (tcam or {}).get("Description") or "",
            "neighborhood": (tcam or {}).get("Neighborhood"),
            "lat": lat,
            "lon": lon,
            "snapshot_url": snapshot_url,
            "stream_key": stream_key,
            "hls_url": HLS_TEMPLATE.replace("{stream}", stream_key) if stream_key else None,
            "hls_auth": "none",  # verified: playlist serves bare, CORS *
            "has_stream": None,  # filled by probe
            "traveler_join": "unitid" if tcam else "none",
            "servstat": p.get("SERVSTAT"),
        })

    if probe:
        def probe_one(cam: dict) -> None:
            if not cam["hls_url"]:
                cam["has_stream"] = False
                return
            try:
                r = client.get(cam["hls_url"], timeout=10)
                cam["has_stream"] = r.status_code == 200 and b"#EXTM3U" in r.content[:64]
            except Exception:
                cam["has_stream"] = False

        with ThreadPoolExecutor(max_workers=4) as pool:  # rate discipline
            list(pool.map(probe_one, cameras))

    manifest = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "hls_template": HLS_TEMPLATE,
        "counts": {
            "total": len(cameras),
            "sdot": sum(1 for c in cameras if c["ownership"] == "SDOT"),
            "wsdot": sum(1 for c in cameras if c["ownership"] == "WSDOT"),
            "with_stream": sum(1 for c in cameras if c["has_stream"]),
            "traveler_joined": sum(1 for c in cameras if c["traveler_join"] != "none"),
        },
        "cameras": cameras,
    }
    DATA.mkdir(exist_ok=True)
    (DATA / "cameras.json").write_text(json.dumps(manifest, indent=1))
    return manifest


if __name__ == "__main__":
    m = build(probe="--no-probe" not in sys.argv)
    print(json.dumps(m["counts"], indent=2))
