"""Mathematics layer: pixel-space detections -> real lat/lon estimates.

Takes a CNN detection (normalized bbox in a camera frame) plus everything
the graph already knows about that camera — resolved bearing (manual >
sat-VLM > sun layers), OSM road axis fallback, camera lat/lon — and returns
a world-position estimate:

  bearing:  proper perspective projection, atan((px - cx) / f_px) off the
            camera's compass bearing, f_px derived from the assumed HFOV
  range:    pinhole known-height — real-world object height (person 1.7 m,
            car 1.5 m, ...) times focal length over bbox pixel height;
            the bbox BOTTOM edge is where the object meets the ground
  position: dest_point(camera, bearing, range)

These are monocular estimates from a single uncalibrated camera; every
output carries method + pos_conf + the bearing basis it leaned on, and when
the camera's direction is completely unknown we return no position rather
than a fabricated one (an axis-only bearing IS used, but flagged and
down-weighted — the caller/UI can style it accordingly).
"""
from __future__ import annotations

import math

from . import config
from .graph import dest_point

# real-world heights (meters) for the pinhole range estimate
KNOWN_HEIGHT_M = {
    "person": 1.70,
    "bicycle": 1.60,     # with rider
    "motorbike": 1.55,
    "car": 1.50,
    "bus": 3.20,
    "truck": 3.00,
}

# footprint (length, width, height) meters — for the UI's 3D boxes
FOOTPRINT_M = {
    "person": (0.55, 0.55, 1.75),
    "bicycle": (1.8, 0.7, 1.6),
    "motorbike": (2.1, 0.8, 1.55),
    "car": (4.5, 1.9, 1.5),
    "bus": (11.0, 2.55, 3.2),
    "truck": (7.0, 2.5, 3.0),
}


def _effective_bearing(node: dict) -> tuple[float | None, str, float]:
    """(bearing_deg, basis, conf) — resolved bearing first, road axis as a
    flagged fallback, None when the camera's direction is a total unknown."""
    b = node.get("bearing") or {}
    if b.get("bearing_deg") is not None:
        return (b["bearing_deg"], b.get("basis") or "unknown",
                b.get("bearing_conf") or 0.35)
    if node.get("way_dir_deg") is not None:
        return node["way_dir_deg"] % 360.0, "axis-only-unresolved", 0.2
    return None, "no-bearing", 0.0


def place(node: dict, det: dict, img_w: int, img_h: int) -> dict | None:
    """World estimate for one detection {label, conf, box:[x1,y1,x2,y2] norm}.
    Returns {lat, lon, bearing_deg, range_m, pos_conf, method, bearing_basis}
    or None when no direction information exists at all."""
    cam_bearing, basis, bconf = _effective_bearing(node)
    if cam_bearing is None:
        return None

    x1, y1, x2, y2 = det["box"]
    f_px = (img_w / 2.0) / math.tan(math.radians(config.ASSUMED_FOV_DEG) / 2.0)

    # bearing: perspective-correct angle off the optical axis
    cx_px = (x1 + x2) / 2.0 * img_w
    offset_deg = math.degrees(math.atan((cx_px - img_w / 2.0) / f_px))
    obj_bearing = (cam_bearing + offset_deg) % 360.0

    # range: known-height pinhole on bbox pixel height
    h_px = max((y2 - y1) * img_h, 1.0)
    h_real = KNOWN_HEIGHT_M.get(det["label"])
    if h_real is None:
        return None
    range_m = h_real * f_px / h_px
    clamped = not (config.CV_RANGE_MIN_M <= range_m <= config.CV_RANGE_MAX_M)
    range_m = max(config.CV_RANGE_MIN_M, min(config.CV_RANGE_MAX_M, range_m))

    lat, lon = dest_point(node["lat"], node["lon"], obj_bearing, range_m)

    # position confidence: bearing certainty x detection confidence,
    # discounted when the range estimate hit its clamp
    pos_conf = round(bconf * det["conf"] * (0.5 if clamped else 1.0), 3)
    return {
        "lat": round(lat, 6), "lon": round(lon, 6),
        "bearing_deg": round(obj_bearing, 1),
        "range_m": round(range_m, 1),
        "pos_conf": pos_conf,
        "method": "pinhole-height",
        "bearing_basis": basis,
        "range_clamped": clamped,
    }


def place_all(node: dict, detections: list[dict],
              img_w: int, img_h: int) -> list[dict]:
    out = []
    for det in detections:
        est = place(node, det, img_w, img_h)
        out.append({**det, "est": est,
                    "footprint_m": FOOTPRINT_M.get(det["label"])})
    return out
