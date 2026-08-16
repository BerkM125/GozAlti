"""M3: pair classification and continuity scoring.

For a candidate pair (A, B) on the same street:

  heading_delta < 45           -> CO_DIRECTIONAL, attempt continuity stitch
  135 < heading_delta <= 180   -> OPPOSED (facing each other), split screen
  otherwise                    -> OBLIQUE, split screen
  low confidence / stale / pan -> UNKNOWN, split screen

When a camera's 180-deg direction is unresolved (two-way street, no
usable flow), both hypotheses are tested and the pair confidence is
capped at 0.55 with the basis stated in the reason string.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from app.geometry import ViewGeometry

SCORE_THRESHOLD = 0.6
CONF_GATE = 0.4
STALE_BEARING_DEG = 25.0
STALE_VPX = 0.25


def ang_diff(a: float, b: float) -> float:
    d = abs(a - b) % 360.0
    return d if d <= 180.0 else 360.0 - d


@dataclass
class PairDecision:
    relationship: str          # CO_DIRECTIONAL | OPPOSED | OBLIQUE | UNKNOWN
    layout: str                # STACKED_CONTINUITY | SPLIT
    score: float
    components: dict
    reason: str
    heading_delta: float | None
    upstream: str | None       # camera_id shown on the BOTTOM pane
    downstream: str | None     # camera_id shown on the TOP pane
    direction_basis: str
    notes: list[str] = field(default_factory=list)


def _azimuth(ax: float, ay: float, bx: float, by: float) -> float:
    """Compass azimuth from A to B in the local xy frame (x east, y north)."""
    return math.degrees(math.atan2(bx - ax, by - ay)) % 360.0


def _distance_plausibility(gap_m: float) -> float:
    """Peaks around 100-250 m, decays outside."""
    if 100.0 <= gap_m <= 250.0:
        return 1.0
    if gap_m < 100.0:
        return max(0.0, (gap_m - 40.0) / 60.0)
    return max(0.0, 1.0 - (gap_m - 250.0) / 300.0)


def _continuity_score(gA: ViewGeometry, gB: ViewGeometry,
                      bearing_a: float, bearing_b: float,
                      gap_m: float) -> tuple[float, dict]:
    hd = ang_diff(bearing_a, bearing_b)
    heading = max(0.0, 1.0 - hd / 45.0)

    if gA.vanishing_point and gB.vanishing_point:
        vpx = max(0.0, 1.0 - abs(gA.vanishing_point[0] - gB.vanishing_point[0]) / 0.5)
    else:
        vpx = 0.0

    if gA.horizon_y is not None and gB.horizon_y is not None:
        horizon = max(0.0, 1.0 - abs(gA.horizon_y - gB.horizon_y) / 0.4)
    else:
        horizon = 0.0

    slope = 1.0 if gA.slope_sign == gB.slope_sign else 0.0
    dist = _distance_plausibility(gap_m)

    components = {
        "heading_agreement": round(heading, 3),
        "vp_x_alignment": round(vpx, 3),
        "horizon_agreement": round(horizon, 3),
        "slope_sign_agreement": slope,
        "distance_plausibility": round(dist, 3),
    }
    score = (0.35 * heading + 0.25 * vpx + 0.15 * horizon +
             0.15 * slope + 0.10 * dist)
    return round(score, 3), components


def is_stale(prev: ViewGeometry | None, cur: ViewGeometry) -> str | None:
    """PTZ drift: recomputed geometry disagreeing with the previous sample."""
    if cur.flow and cur.flow.get("pan"):
        return "pan detected in live flow (camera is being re-aimed)"
    if prev is None or prev.image_hash == cur.image_hash:
        return None
    if (prev.vanishing_point and cur.vanishing_point
            and abs(prev.vanishing_point[0] - cur.vanishing_point[0]) > STALE_VPX):
        return (f"VP shifted {abs(prev.vanishing_point[0] - cur.vanishing_point[0]):.2f} "
                "of frame width since last sample")
    if (prev.direction_resolved and cur.direction_resolved
            and ang_diff(prev.road_axis_bearing_deg, cur.road_axis_bearing_deg) > STALE_BEARING_DEG):
        return (f"bearing moved {ang_diff(prev.road_axis_bearing_deg, cur.road_axis_bearing_deg):.0f} deg "
                "since last sample")
    return None


def classify_pair(cam_a: dict, cam_b: dict,
                  gA: ViewGeometry, gB: ViewGeometry,
                  gap_m: float,
                  stale_a: str | None = None,
                  stale_b: str | None = None) -> PairDecision:
    notes: list[str] = []

    # --- gates ------------------------------------------------------------
    for cid, stale in ((cam_a["camera_id"], stale_a), (cam_b["camera_id"], stale_b)):
        if stale:
            return PairDecision(
                relationship="UNKNOWN", layout="SPLIT", score=0.0, components={},
                reason=f"STALE_GEOMETRY on {cid}: {stale} — split screen until two consecutive samples agree",
                heading_delta=None, upstream=None, downstream=None,
                direction_basis="stale")

    conf = min(gA.confidence, gB.confidence)
    if conf < CONF_GATE:
        weak = cam_a["camera_id"] if gA.confidence <= gB.confidence else cam_b["camera_id"]
        return PairDecision(
            relationship="UNKNOWN", layout="SPLIT", score=0.0, components={},
            reason=f"UNKNOWN: geometry confidence {conf:.2f} < {CONF_GATE} "
                   f"(weakest: {weak}) — honest split screen",
            heading_delta=None, upstream=None, downstream=None,
            direction_basis="low-confidence")

    # --- candidate bearings -----------------------------------------------
    def hypotheses(g: ViewGeometry) -> list[float]:
        b = g.road_axis_bearing_deg or 0.0
        return [b] if g.direction_resolved else [b, (b + 180.0) % 360.0]

    az_ab = _azimuth(cam_a["snap_x"], cam_a["snap_y"],
                     cam_b["snap_x"], cam_b["snap_y"])

    both_resolved = gA.direction_resolved and gB.direction_resolved
    best = None
    for ba in hypotheses(gA):
        for bb in hypotheses(gB):
            hd = ang_diff(ba, bb)
            score, comps = _continuity_score(gA, gB, ba, bb, gap_m)
            cand = {"ba": ba, "bb": bb, "hd": hd, "score": score, "comps": comps}
            if best is None or (cand["hd"] < 45.0) > (best["hd"] < 45.0) or (
                    (cand["hd"] < 45.0) == (best["hd"] < 45.0) and cand["score"] > best["score"]):
                best = cand

    hd = best["hd"]
    basis = "flow/map-resolved" if both_resolved else "hypothesis-tested (direction unresolved on two-way street)"
    if not both_resolved:
        notes.append("view direction inferred by testing both 180-deg hypotheses; capped confidence")

    # --- relationship branch ------------------------------------------------
    if hd < 45.0:
        relationship = "CO_DIRECTIONAL"
    elif 135.0 < hd <= 180.0:
        relationship = "OPPOSED"
    else:
        relationship = "OBLIQUE"

    if relationship == "OPPOSED":
        return PairDecision(
            relationship="OPPOSED", layout="SPLIT", score=0.0,
            components=best["comps"],
            reason=f"OPPOSED: heading delta {hd:.0f} deg — cameras face each other, "
                   "mirrored views are never stitchable; plain split screen",
            heading_delta=round(hd, 1), upstream=None, downstream=None,
            direction_basis=basis, notes=notes)

    if relationship == "OBLIQUE":
        return PairDecision(
            relationship="OBLIQUE", layout="SPLIT", score=0.0,
            components=best["comps"],
            reason=f"OBLIQUE: heading delta {hd:.0f} deg — views diverge, no shared corridor",
            heading_delta=round(hd, 1), upstream=None, downstream=None,
            direction_basis=basis, notes=notes)

    # CO_DIRECTIONAL: score it
    score = best["score"]
    if not both_resolved:
        score = round(min(score, 0.55 + 0.45 * best["comps"]["vp_x_alignment"] * 0.5), 3)

    # Upstream = camera looking TOWARD the other; downstream shows the far road.
    shared = best["ba"]
    if ang_diff(shared, az_ab) < 90.0:
        upstream, downstream = cam_a["camera_id"], cam_b["camera_id"]
    else:
        upstream, downstream = cam_b["camera_id"], cam_a["camera_id"]

    if score >= SCORE_THRESHOLD:
        return PairDecision(
            relationship="CO_DIRECTIONAL", layout="STACKED_CONTINUITY",
            score=score, components=best["comps"],
            reason=f"CO_DIRECTIONAL: heading delta {hd:.0f} deg, continuity score "
                   f"{score:.2f} >= {SCORE_THRESHOLD} — stacking (downstream {downstream} on top, "
                   f"upstream {upstream} below); direction basis: {basis}",
            heading_delta=round(hd, 1), upstream=upstream, downstream=downstream,
            direction_basis=basis, notes=notes)

    return PairDecision(
        relationship="CO_DIRECTIONAL", layout="SPLIT",
        score=score, components=best["comps"],
        reason=f"CO_DIRECTIONAL but weak: continuity score {score:.2f} < {SCORE_THRESHOLD} "
               f"(heading delta {hd:.0f} deg) — split screen; direction basis: {basis}",
        heading_delta=round(hd, 1), upstream=upstream, downstream=downstream,
        direction_basis=basis, notes=notes)
