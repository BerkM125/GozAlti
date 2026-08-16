#!/usr/bin/env python3
"""Multi-object tracker: stitch per-frame person boxes into tracks. Stdlib only.

Why this exists: per-frame counts undercount a street. A camera that shows 6 people
in every frame for a minute has not seen 6 people, it has seen everyone who walked
past. Peak-occupancy is the wrong headline; unique tracks is the right one, and it
also buys dwell time and direction of travel for free.

Matching is greedy IoU with a constant-velocity prediction and a centroid fallback
gated in body-heights, because at the 2 fps we sample a walker moves roughly 0.4 of
their own height between frames and IoU alone drops out. No scipy, no filterpy —
the assignment is small enough (tens of boxes) that greedy is exact enough and the
box has neither library.

Everything reported here is geometry measured off the boxes. The tracker never
decides whether anything it sees is good, bad, or dangerous.
"""
import math

# Motion cuts, in body-heights per second (a person's own height is the only scale
# a single fixed camera gives us for free, and it cancels perspective). A 1.7 m
# person walking 1.4 m/s covers ~0.8 body-heights/s; standing still is ~0.
WALK_BH_S = 0.35
STILL_BH_S = 0.12


def iou(a, b):
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    ix1, iy1 = max(ax1, bx1), max(ay1, by1)
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)
    iw, ih = max(0.0, ix2 - ix1), max(0.0, iy2 - iy1)
    inter = iw * ih
    if inter <= 0:
        return 0.0
    ua = (ax2 - ax1) * (ay2 - ay1) + (bx2 - bx1) * (by2 - by1) - inter
    return inter / ua if ua > 0 else 0.0


class Track:
    __slots__ = ("id", "obs", "misses", "vx", "vy", "last_box")

    def __init__(self, tid, frame_idx, t, det):
        self.id = tid
        self.obs = []          # [{i, t, box, cx, cy, h, conf, facing, n_keypoints}]
        self.misses = 0
        self.vx = self.vy = 0.0
        self.last_box = det["box"]
        self.add(frame_idx, t, det)

    def add(self, frame_idx, t, det):
        if self.obs:
            prev = self.obs[-1]
            dt = max(1e-6, t - prev["t"])
            self.vx = (det["cx"] - prev["cx"]) / dt
            self.vy = (det["cy"] - prev["cy"]) / dt
        self.obs.append({"i": frame_idx, "t": round(t, 3), "box": det["box"],
                         "cx": det["cx"], "cy": det["cy"], "h": det.get("h", 0.0),
                         "conf": det["conf"], "facing": det.get("facing"),
                         "n_keypoints": det.get("n_keypoints")})
        self.last_box = det["box"]
        self.misses = 0

    def predict_box(self, t, W, H):
        """Where the box should be at time t, at constant velocity. Normalised in, px out."""
        if not self.obs:
            return self.last_box
        last = self.obs[-1]
        dt = t - last["t"]
        dx, dy = self.vx * dt * W, self.vy * dt * H
        x1, y1, x2, y2 = last["box"]
        return [x1 + dx, y1 + dy, x2 + dx, y2 + dy]


class Tracker:
    """Greedy IoU + predicted-centroid tracker.

    iou_thresh    accept a match on overlap alone at or above this
    gate_bh       else accept if the centroid is within this many body-heights of
                  the predicted position (0.9 covers a brisk walk at 2 fps)
    max_age       how many consecutive sampled frames a track may go unseen
    min_hits      how many observations before a track counts as a real person
    """

    def __init__(self, iou_thresh=0.20, gate_bh=0.9, max_age=3, min_hits=2):
        self.iou_thresh, self.gate_bh = iou_thresh, gate_bh
        self.max_age, self.min_hits = max_age, min_hits
        self.tracks, self.done, self._next = [], [], 1

    def update(self, frame_idx, t, dets, W, H):
        """Feed one frame's people. Returns track id per detection, aligned to `dets`."""
        assign = [None] * len(dets)
        pairs = []
        for ti, tr in enumerate(self.tracks):
            pb = tr.predict_box(t, W, H)
            for di, d in enumerate(dets):
                ov = iou(pb, d["box"])
                # body-height-normalised centroid distance to the predicted position
                pcx = (pb[0] + pb[2]) / 2 / W
                pcy = (pb[1] + pb[3]) / 2 / H
                h = max(d.get("h") or 0.0, tr.obs[-1]["h"], 0.02)
                dist = math.hypot(d["cx"] - pcx, d["cy"] - pcy) / h
                if ov >= self.iou_thresh:
                    pairs.append((-ov, ti, di))            # best overlap first
                elif dist <= self.gate_bh:
                    pairs.append((dist, ti, di))           # then nearest, still gated
        pairs.sort()
        used_t, used_d = set(), set()
        for _, ti, di in pairs:
            if ti in used_t or di in used_d:
                continue
            used_t.add(ti); used_d.add(di)
            self.tracks[ti].add(frame_idx, t, dets[di])
            assign[di] = self.tracks[ti].id
        for ti, tr in enumerate(self.tracks):
            if ti not in used_t:
                tr.misses += 1
        for di, d in enumerate(dets):
            if di not in used_d:
                tr = Track(self._next, frame_idx, t, d)
                self._next += 1
                self.tracks.append(tr)
                assign[di] = tr.id
        alive = []
        for tr in self.tracks:
            (alive if tr.misses <= self.max_age else self.done).append(tr)
        self.tracks = alive
        return assign

    def finish(self):
        self.done += self.tracks
        self.tracks = []
        return self.done

    def stitch(self, max_gap_s=1.5, gate_bh=1.4, size_ratio=1.7):
        """Rejoin tracks that the same person left behind when they were briefly lost.

        Fragmentation is the one thing that can inflate "unique people" past the truth:
        a walker who passes behind a bus for three sampled frames comes back as a new
        id, and the headline number quietly counts them twice. So after tracking, any
        track that ENDS is offered to any track that STARTS shortly after, and they are
        joined when the second one begins near where the first was heading, at a
        compatible size. Best-scoring pair first, repeat until nothing merges.

        The gates are deliberately tight — a wrong join merges two real people into
        one and undercounts, which is just as dishonest as overcounting.
        """
        tracks = [t for t in self.done if t]
        joins = 0
        while True:
            cands = []
            for A in tracks:
                for B in tracks:
                    if A is B:
                        continue
                    gap = B.obs[0]["t"] - A.obs[-1]["t"]
                    if not (0 < gap <= max_gap_s):
                        continue
                    la, fb = A.obs[-1], B.obs[0]
                    px, py = la["cx"] + A.vx * gap, la["cy"] + A.vy * gap
                    h = max(la["h"], fb["h"], 0.02)
                    dist = math.hypot(fb["cx"] - px, fb["cy"] - py) / h
                    lo, hi = sorted((max(la["h"], 1e-6), max(fb["h"], 1e-6)))
                    if dist <= gate_bh and hi / lo <= size_ratio:
                        cands.append((dist, id(A), id(B), A, B))
            if not cands:
                break
            cands.sort(key=lambda c: c[0])
            used = set()
            did = 0
            for _, ia, ib, A, B in cands:
                if ia in used or ib in used:
                    continue
                used.add(ia); used.add(ib)
                A.obs.extend(B.obs)
                A.obs.sort(key=lambda o: o["t"])
                tracks.remove(B)
                did += 1
            joins += did
            if not did:
                break
        self.done = tracks
        self.joins = joins
        return joins

    def summary(self, fps_sampled, stitch=True):
        """One record per track: dwell, travel, speed, motion class, facing."""
        self.finish()
        if stitch:
            self.stitch()
        out = []
        for tr in sorted(self.done, key=lambda t: t.id):
            n = len(tr.obs)
            first, last = tr.obs[0], tr.obs[-1]
            dwell = round(last["t"] - first["t"], 2)
            # a single-frame track has zero span; it was still on screen for one sample
            span = dwell if dwell > 0 else round(1.0 / fps_sampled, 2)
            dx, dy = last["cx"] - first["cx"], last["cy"] - first["cy"]
            net = math.hypot(dx, dy)
            path = 0.0
            for a, b in zip(tr.obs, tr.obs[1:]):
                path += math.hypot(b["cx"] - a["cx"], b["cy"] - a["cy"])
            mean_h = sum(o["h"] for o in tr.obs) / n or 0.02
            # speed in body-heights/s along the actual path, not the net displacement
            speed_bh = (path / mean_h / span) if span > 0 and mean_h > 0 else 0.0
            motion = ("walking" if speed_bh >= WALK_BH_S else
                      "standing" if speed_bh < STILL_BH_S else "slow_moving")
            deg = (math.degrees(math.atan2(-dy, dx)) % 360) if net > 0.01 else None
            faces = [o["facing"] for o in tr.obs if o.get("facing")]
            facing_mode = max(set(faces), key=faces.count) if faces else None
            out.append({
                "id": tr.id, "frames": n, "confirmed": n >= self.min_hits,
                "t_start": first["t"], "t_end": last["t"], "dwell_s": dwell,
                "mean_conf": round(sum(o["conf"] for o in tr.obs) / n, 3),
                "mean_height_frac": round(mean_h, 4),
                "path_frac": round(path, 4), "net_frac": round(net, 4),
                "straightness": round(net / path, 3) if path > 1e-6 else None,
                "speed_bh_s": round(speed_bh, 3),
                "motion": motion,
                "travel_deg": round(deg, 1) if deg is not None else None,
                "travel_label": _compass(deg, net),
                "facing": facing_mode,
                "keypoint_frames": sum(1 for o in tr.obs if o.get("n_keypoints")),
                "start_xy": [first["cx"], first["cy"]],
                "end_xy": [last["cx"], last["cy"]],
                "path_xy": [[o["cx"], o["cy"]] for o in tr.obs],
            })
        return out


def _compass(deg, net):
    """Image-plane direction of travel. NOT a real-world bearing — we have no homography."""
    if deg is None or net < 0.01:
        return "stationary"
    names = [(22.5, "right"), (67.5, "up-right"), (112.5, "up-frame"), (157.5, "up-left"),
             (202.5, "left"), (247.5, "down-left"), (292.5, "down-frame"),
             (337.5, "down-right"), (360.1, "right")]
    for lim, name in names:
        if deg < lim:
            return name
    return "right"


def aggregate(per_frame, tracks, fps_sampled, duration):
    """Clip-level headline numbers. Every one of these is counted, none is modelled."""
    counts = [f["people_count"] for f in per_frame]
    vcounts = [f["vehicle_count"] for f in per_frame]
    confirmed = [t for t in tracks if t["confirmed"]]
    dwells = sorted(t["dwell_s"] for t in confirmed)
    peak_i = counts.index(max(counts)) if counts else 0
    motions = {}
    for t in confirmed:
        motions[t["motion"]] = motions.get(t["motion"], 0) + 1
    travel = {}
    for t in confirmed:
        travel[t["travel_label"]] = travel.get(t["travel_label"], 0) + 1
    facing = {}
    for t in confirmed:
        if t["facing"]:
            facing[t["facing"]] = facing.get(t["facing"], 0) + 1
    return {
        "frames_sampled": len(per_frame), "sample_fps": fps_sampled,
        "duration_s": round(duration, 2),
        "people_peak": max(counts) if counts else 0,
        "people_peak_at_s": round(per_frame[peak_i]["t"], 2) if per_frame else 0,
        "people_mean": round(sum(counts) / len(counts), 2) if counts else 0,
        "people_min": min(counts) if counts else 0,
        "vehicles_peak": max(vcounts) if vcounts else 0,
        "vehicles_mean": round(sum(vcounts) / len(vcounts), 2) if vcounts else 0,
        "tracks_total": len(tracks),
        "unique_people": len(confirmed),
        "unique_people_note": f"tracks seen in >= 2 sampled frames; "
                              f"{len(tracks) - len(confirmed)} single-frame tracks excluded",
        "dwell_median_s": dwells[len(dwells) // 2] if dwells else 0,
        "dwell_max_s": dwells[-1] if dwells else 0,
        "motion_mix": motions, "travel_mix": travel, "facing_mix": facing,
    }
