"""Camera metadata: street/area -> nearby cameras.

Ported from surukamera's camera<->street snapping (app/streets.py) and camera
manifest (app/manifest.py) — 650 SDOT/WSDOT cameras, 472 snapped to a named
street with an OSM-derived road axis.

Bearings here are that road axis only. The full VLM/optical-flow bearing
stack (app/bearing.py) resolves *which* of the axis's two 180-degree
directions the camera actually faces using live snapshot fetches, satellite
tiles, and a VLM — all out of scope for this module ("no ML, no fetching
imagery" per SPEC.md). A bare road axis is direction-ambiguous, so
bearing_conf here is always the stack's own "unresolved" tier (0.35), never
higher — honest about what this module actually knows, not a claim about
which way the lens points. vlm/osint can raise it later by writing a resolved
bearing next to road_axis_deg in data/streets.json; this module would then
prefer it, no interface change needed.

    cp ../../experiments/surukamera/data/cameras.json data/cameras.json  # refresh
    cp ../../experiments/surukamera/data/streets.json data/streets.json  # refresh
"""

import json
import pathlib

from ._geo import haversine

ROOT = pathlib.Path(__file__).resolve().parent.parent
CAMERAS_PATH = ROOT / "data" / "cameras.json"
STREETS_PATH = ROOT / "data" / "streets.json"

DEFAULT_RADIUS_M = 300.0
UNRESOLVED_BEARING_CONF = 0.35


class _Cameras:
    def __init__(self, cameras_path: pathlib.Path = CAMERAS_PATH, streets_path: pathlib.Path = STREETS_PATH):
        manifest = json.loads(cameras_path.read_text())
        snapped = json.loads(streets_path.read_text())

        # Full 650-camera manifest is the base; overlay street_name/
        # road_axis_deg for the 472 that snapped to a named street.
        self.by_id: dict[str, dict] = {c["camera_id"]: c for c in manifest["cameras"]}
        for cam_id, snap in snapped["cameras"].items():
            self.by_id.setdefault(cam_id, snap).update(
                {k: snap[k] for k in ("street_name", "road_axis_deg") if k in snap}
            )
        self.street_index: dict[str, list[str]] = snapped["street_index"]
        self._norm = {name.casefold(): name for name in self.street_index}

    def _record(self, cam: dict) -> dict:
        bearing = cam.get("road_axis_deg")
        return {
            "camera_id": cam["camera_id"],
            "lat": cam["lat"],
            "lon": cam["lon"],
            "bearing_deg": bearing,
            "bearing_conf": UNRESOLVED_BEARING_CONF if bearing is not None else 0.0,
            "live_hls": cam["hls_url"] if cam.get("has_stream") else None,
            "snapshot_url": cam.get("snapshot_url"),
        }

    def _resolve_street(self, street: str) -> str | None:
        name = self._norm.get(street.casefold())
        if name is not None:
            return name
        hits = [n for n in self.street_index if street.casefold() in n.casefold()]
        return hits[0] if hits else None

    def by_street(self, street: str) -> list[dict]:
        name = self._resolve_street(street)
        if name is None:
            return []
        return [self._record(self.by_id[cid]) for cid in self.street_index[name]]

    def near(self, lat: float, lon: float, radius_m: float = DEFAULT_RADIUS_M) -> list[dict]:
        out = []
        for cam in self.by_id.values():
            if haversine((lon, lat), (cam["lon"], cam["lat"])) <= radius_m:
                out.append(self._record(cam))
        return out


_CAMERAS: _Cameras | None = None


def _cameras() -> _Cameras:
    global _CAMERAS
    if _CAMERAS is None:
        _CAMERAS = _Cameras()
    return _CAMERAS


def cameras_for(query: dict) -> dict:
    """Contract entry point for CameraConvergence (SPEC.md §6.7).

    query is {"street": <name>} (substring-forgiving, e.g. "pike" matches
    "Pike Street") or {"lat":..., "lon":..., "radius_m": <optional>}.
    """
    c = _cameras()
    if "street" in query:
        cams = c.by_street(query["street"])
    elif "lat" in query and "lon" in query:
        cams = c.near(query["lat"], query["lon"], query.get("radius_m", DEFAULT_RADIUS_M))
    else:
        raise ValueError("query must have 'street' or 'lat'/'lon'")
    return {"query": query, "cameras": cams}
