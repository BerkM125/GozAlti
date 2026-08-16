"""VLM bearing layers (optional — behind an API key).

Two estimators, both using Claude vision with strict enum-only JSON schemas:

  * landmark_bearing(): which Seattle landmarks are visible and where in
    frame (left/center/right). Bearing from camera lat/lon to landmark is
    known, so heading ~= bearing_to_landmark - frame_offset.
  * satellite_cross_check(): camera frame + annotated satellite crop with
    two arrows (the two 180-deg axis hypotheses); the model picks which
    arrow matches the view, or neither.

Gating: needs VISION_API_KEY or ANTHROPIC_API_KEY in the environment and
the `anthropic` package installed; otherwise every function returns None
silently — the app must fully work without it.

Constraint (from the project spec): enum-only schemas, nothing free-text
reaches the layout engine, and prompts must not request any description of
people, vehicle occupants, or activity. Road structure and skyline only.
"""
from __future__ import annotations

import base64
import json
import math
import os
from functools import lru_cache

from app import netboot

# Seattle landmarks with known coordinates (point landmarks) or fixed
# compass azimuths (distant ranges, valid anywhere in the city).
LANDMARKS_POINT = {
    "space_needle": (47.6205, -122.3493),
    "smith_tower": (47.6018, -122.3318),
    "columbia_center": (47.6045, -122.3308),
    "t_mobile_park": (47.5914, -122.3325),
    "lumen_field": (47.5952, -122.3316),
}
LANDMARKS_AZIMUTH = {
    "mount_rainier": 157.0,     # SSE of Seattle
    "olympic_mountains": 270.0,  # W
    "cascade_range": 90.0,       # E
    "elliott_bay": None,         # direction depends on position; skip azimuth
}
POSITION_OFFSET = {"left": -25.0, "center": 0.0, "right": 25.0}

LANDMARK_SCHEMA = {
    "type": "object",
    "properties": {
        "visible_landmarks": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "enum": sorted(
                        list(LANDMARKS_POINT) + [k for k in LANDMARKS_AZIMUTH if LANDMARKS_AZIMUTH[k]]
                    )},
                    "position": {"type": "string", "enum": ["left", "center", "right"]},
                },
                "required": ["name", "position"],
                "additionalProperties": False,
            },
        },
        "road_recedes_toward": {"type": "string",
                                "enum": ["top-left", "top-center", "top-right", "not-visible"]},
        "grade": {"type": "string", "enum": ["rises_away", "falls_away", "flat", "unclear"]},
        "view_blocked": {"type": "boolean"},
    },
    "required": ["visible_landmarks", "road_recedes_toward", "grade", "view_blocked"],
    "additionalProperties": False,
}

SATELLITE_SCHEMA = {
    "type": "object",
    "properties": {
        "matching_arrow": {"type": "string", "enum": ["A", "B", "neither", "unclear"]},
        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
    },
    "required": ["matching_arrow", "confidence"],
    "additionalProperties": False,
}


@lru_cache(maxsize=1)
def _client():
    key = os.environ.get("VISION_API_KEY") or os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        return None
    try:
        import anthropic
        from anthropic import DefaultHttpxClient
    except ImportError:
        return None
    # Route through the in-process DNS-over-TCP proxy (UDP/53 is blocked here)
    return anthropic.Anthropic(
        api_key=key,
        http_client=DefaultHttpxClient(proxy=netboot.ensure_proxy()),
    )


def available() -> bool:
    return _client() is not None


def _img_block(jpeg_bytes: bytes) -> dict:
    return {
        "type": "image",
        "source": {
            "type": "base64",
            "media_type": "image/jpeg",
            "data": base64.standard_b64encode(jpeg_bytes).decode(),
        },
    }


def _bearing_to(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    dlon = math.radians(lon2 - lon1)
    la1, la2 = math.radians(lat1), math.radians(lat2)
    x = math.sin(dlon) * math.cos(la2)
    y = math.cos(la1) * math.sin(la2) - math.sin(la1) * math.cos(la2) * math.cos(dlon)
    return math.degrees(math.atan2(x, y)) % 360.0


def _query(schema: dict, prompt: str, images: list[bytes]) -> dict | None:
    client = _client()
    if client is None:
        return None
    try:
        resp = client.messages.create(
            model="claude-opus-5",
            max_tokens=1024,
            output_config={"format": {"type": "json_schema", "schema": schema}},
            messages=[{
                "role": "user",
                "content": [*(_img_block(b) for b in images),
                            {"type": "text", "text": prompt}],
            }],
        )
        if resp.stop_reason == "refusal":
            return None
        text = next((b.text for b in resp.content if b.type == "text"), None)
        return json.loads(text) if text else None
    except Exception:
        return None


def landmark_bearing(frame_jpeg: bytes, cam_lat: float, cam_lon: float) -> dict | None:
    """Estimate compass heading from visible landmarks. Returns
    {"bearing_deg", "n_landmarks", "raw"} or None."""
    prompt = (
        "This is a public traffic-camera frame from Seattle, used for a "
        "road-geometry study. Identify ONLY the listed Seattle landmarks / "
        "mountain ranges if clearly visible in the skyline or background, "
        "and where each sits in the frame (left/center/right third). Also "
        "report which direction the main road recedes toward, the road "
        "grade, and whether the view is blocked. Do not describe people, "
        "vehicles, or activity — road structure and skyline only."
    )
    out = _query(LANDMARK_SCHEMA, prompt, [frame_jpeg])
    if not out or out.get("view_blocked"):
        return None
    xs, ys = [], []
    for lm in out.get("visible_landmarks", []):
        name = lm["name"]
        if name in LANDMARKS_POINT:
            az = _bearing_to(cam_lat, cam_lon, *LANDMARKS_POINT[name])
        elif LANDMARKS_AZIMUTH.get(name) is not None:
            az = LANDMARKS_AZIMUTH[name]
        else:
            continue
        heading = (az - POSITION_OFFSET[lm["position"]]) % 360.0
        xs.append(math.sin(math.radians(heading)))
        ys.append(math.cos(math.radians(heading)))
    if not xs:
        return None
    bearing = math.degrees(math.atan2(sum(xs) / len(xs), sum(ys) / len(ys))) % 360.0
    return {"bearing_deg": round(bearing, 1), "n_landmarks": len(xs), "raw": out}


def satellite_cross_check(frame_jpeg: bytes, satellite_png: bytes,
                          dir_a: float, dir_b: float) -> dict | None:
    """Camera frame + satellite crop annotated with arrows A/B (the two
    axis hypotheses). Returns {"bearing_deg", "arrow", "confidence"} or None."""
    prompt = (
        "Image 1 is a Seattle traffic-camera frame; image 2 is a satellite "
        "view of the same intersection with two arrows, A and B, marking "
        "the two possible viewing directions of the camera along the road "
        "axis. Compare the buildings, road layout, and surroundings: which "
        "arrow direction matches what the camera sees? Judge from road "
        "structure and building positions only; do not describe people or "
        "vehicles."
    )
    out = _query(SATELLITE_SCHEMA, prompt, [frame_jpeg, satellite_png])
    if not out or out.get("matching_arrow") in (None, "neither", "unclear"):
        return None
    bearing = dir_a if out["matching_arrow"] == "A" else dir_b
    return {"bearing_deg": round(bearing % 360.0, 1),
            "arrow": out["matching_arrow"], "confidence": out["confidence"]}
