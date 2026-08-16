#!/usr/bin/env python3
"""Shared detector logic: model loading, inference, scoring, ranking, drawing.

detect.py (stills) and video.py (clips) both import this. Nothing here talks to a
VLM and nothing here judges anything — it produces counts, boxes, and, when the
architecture supports it, silhouette masks and 17-point skeletons.

Three person-capable architectures, all COCO-pretrained torchvision weights that
are already in ~/junk/torchcache on the box:

  fasterrcnn   fasterrcnn_resnet50_fpn_v2   boxes only     people + vehicles
  maskrcnn     maskrcnn_resnet50_fpn_v2     + pixel masks  people + vehicles
  keypointrcnn keypointrcnn_resnet50_fpn    + 17 keypoints PEOPLE ONLY (person class)

Measured on the GB10 over 23 stills (warmup per config, min-of-2):
fasterrcnn 64.4 ms, maskrcnn 72.2 ms, keypointrcnn 60.1 ms. The keypoint model is
the cheapest because it only has one class to score, and it is the only one that
gives facing direction.

Input resolution is currently irrelevant to cost and to recall: torchvision's
detection transform resizes internally to min_size=800 / max_size=1333, so a 1080p
frame and a 1024-capped frame become the same tensor (measured: 64.4 vs 64.6 ms,
133 vs 134 people). `min_size=` on the loader is the knob that actually changes it.
"""
import math, time
from pathlib import Path

VEHICLE_CLASSES = {"car", "truck", "bus", "motorcycle", "bicycle", "train"}
REPORT_VEHICLES = ("car", "truck", "bus", "motorcycle", "bicycle", "train")

# COCO 17-keypoint order, as torchvision emits them
KP_NAMES = ("nose", "left_eye", "right_eye", "left_ear", "right_ear",
            "left_shoulder", "right_shoulder", "left_elbow", "right_elbow",
            "left_wrist", "right_wrist", "left_hip", "right_hip",
            "left_knee", "right_knee", "left_ankle", "right_ankle")
KP = {n: i for i, n in enumerate(KP_NAMES)}
# torchvision keypoint "scores" are unbounded per-point logits; ~2.0 is the usual
# visible/not-visible cut and is what the torchvision reference visualiser uses.
KP_VISIBLE = 2.0

ARCHS = ("fasterrcnn", "maskrcnn", "keypointrcnn", "retinanet", "fcos")


class Detector:
    """A loaded torchvision detector plus the metadata the callers need."""

    def __init__(self, arch, model, transforms, names, device, min_size):
        self.arch, self.model, self.tf = arch, model, transforms
        self.names, self.device, self.min_size = names, device, min_size
        self.has_masks = arch == "maskrcnn"
        self.has_keypoints = arch == "keypointrcnn"
        self.people_only = arch == "keypointrcnn"

    def __repr__(self):
        return f"<Detector {self.arch} min_size={self.min_size} on {self.device}>"


def load(arch, thresh=0.5, device="cuda", min_size=None):
    """Build one detector. `min_size` overrides torchvision's internal 800 px resize."""
    from torchvision.models import detection as D
    table = {
        "fasterrcnn":   (D.fasterrcnn_resnet50_fpn_v2, D.FasterRCNN_ResNet50_FPN_V2_Weights),
        "maskrcnn":     (D.maskrcnn_resnet50_fpn_v2,   D.MaskRCNN_ResNet50_FPN_V2_Weights),
        "keypointrcnn": (D.keypointrcnn_resnet50_fpn,  D.KeypointRCNN_ResNet50_FPN_Weights),
        "retinanet":    (D.retinanet_resnet50_fpn_v2,  D.RetinaNet_ResNet50_FPN_V2_Weights),
        "fcos":         (D.fcos_resnet50_fpn,          D.FCOS_ResNet50_FPN_Weights),
    }
    if arch not in table:
        raise ValueError(f"unknown arch {arch!r}; pick from {', '.join(table)}")
    fn, wcls = table[arch]
    w = wcls.DEFAULT
    kw = {"weights": w, "box_score_thresh": thresh}
    if min_size:                       # the only knob that makes HD frames matter
        kw["min_size"] = int(min_size)
        kw["max_size"] = int(min_size * 1.6667)   # keep torchvision's 800/1333 ratio
    model = fn(**kw).eval().to(device)
    return Detector(arch, model, w.transforms(), w.meta["categories"], device, min_size or 800)


def read_tensor(det, path):
    """Load an image off disk into the tensor the model wants. Returns (tensor, W, H)."""
    from torchvision.io import read_image
    img = det.tf(read_image(str(path))).to(det.device)
    _, H, W = img.shape
    return img, W, H


def infer(det, tensor):
    """One forward pass. Returns (raw_output, milliseconds) with the GPU synced."""
    import torch
    if det.device == "cuda":
        torch.cuda.synchronize()
    t0 = time.perf_counter()
    with torch.no_grad():
        out = det.model([tensor])[0]
    if det.device == "cuda":
        torch.cuda.synchronize()          # otherwise we time the launch, not the work
    return out, (time.perf_counter() - t0) * 1000


def _facing(pts, scores):
    """Which way is this person facing, from shoulders/hips? Geometry, not opinion.

    In image coordinates a person facing the camera shows their RIGHT shoulder to the
    LEFT of their left shoulder (the mirror flip), so sign(rs.x - ls.x) separates
    toward-camera from away-from-camera. When the shoulders nearly stack up in x the
    person is side-on, and the nose tells us which way along the frame.

    Returns None when the keypoints needed aren't visible. It never guesses.
    """
    def vis(name):
        i = KP[name]
        return pts[i] if scores[i] >= KP_VISIBLE else None
    ls, rs = vis("left_shoulder"), vis("right_shoulder")
    lh, rh = vis("left_hip"), vis("right_hip")
    if not (ls and rs):
        return None
    sx, sy = rs[0] - ls[0], rs[1] - ls[1]
    shoulder_w = math.hypot(sx, sy)
    mid_s = ((ls[0] + rs[0]) / 2, (ls[1] + rs[1]) / 2)
    torso = None
    if lh and rh:
        mid_h = ((lh[0] + rh[0]) / 2, (lh[1] + rh[1]) / 2)
        torso = math.hypot(mid_h[0] - mid_s[0], mid_h[1] - mid_s[1])
    # side-on when the shoulder line is short against the torso; 0.30 is the cut we use
    ratio = (shoulder_w / torso) if torso and torso > 1 else None
    side_on = ratio is not None and ratio < 0.30
    nose = vis("nose")
    if side_on:
        if nose:
            face = "frame_right" if nose[0] > mid_s[0] else "frame_left"
        else:
            face = "side_on"
    else:
        face = "toward_camera" if sx < 0 else "away_from_camera"
    # image-plane angle of the facing normal (perpendicular to the shoulder line)
    deg = round(math.degrees(math.atan2(-sx, sy)) % 360, 1) if shoulder_w > 1 else None
    return {"facing": face, "shoulder_ratio": round(ratio, 3) if ratio else None,
            "facing_deg": deg, "shoulder_px": round(shoulder_w, 1)}


def parse(det, out, W, H, person_thresh=0.5, vehicle_thresh=0.5,
          want_masks=True, mask_polys=False):
    """Raw model output -> (people, vehicles). Same record shape for every arch."""
    boxes = out["boxes"].tolist()
    labels = out["labels"].tolist()
    scores = out["scores"].tolist()
    masks = out.get("masks") if (det.has_masks and want_masks) else None
    kps = out.get("keypoints") if det.has_keypoints else None
    kp_scores = out.get("keypoints_scores") if det.has_keypoints else None

    people, vehicles = [], {k: 0 for k in REPORT_VEHICLES}
    for i, (box, label, sc) in enumerate(zip(boxes, labels, scores)):
        name = det.names[label] if label < len(det.names) else str(label)
        x1, y1, x2, y2 = box
        if name == "person" and sc >= person_thresh:
            rec = {"label": "person", "conf": round(sc, 3),
                   "box": [round(x1), round(y1), round(x2), round(y2)],
                   "cx": round((x1 + x2) / 2 / W, 4), "cy": round((y1 + y2) / 2 / H, 4),
                   "h": round((y2 - y1) / H, 4)}
            if masks is not None:
                m = masks[i, 0]
                binm = m > 0.5
                rec["mask_area"] = int(binm.sum().item())
                rec["mask_fill"] = round(rec["mask_area"] / max(1.0, (x2 - x1) * (y2 - y1)), 3)
                if mask_polys:
                    rec["mask_poly"] = _poly(binm)
            if kps is not None:
                pts = kps[i].tolist()                 # [17][x, y, visibility]
                ks = kp_scores[i].tolist()
                rec["keypoints"] = [[round(p[0]), round(p[1]), round(s, 2)]
                                    for p, s in zip(pts, ks)]
                rec["n_keypoints"] = sum(1 for s in ks if s >= KP_VISIBLE)
                f = _facing([(p[0], p[1]) for p in pts], ks)
                if f:
                    rec.update(f)
            people.append(rec)
        elif name in VEHICLE_CLASSES and sc >= vehicle_thresh:
            vehicles[name] = vehicles.get(name, 0) + 1
    people.sort(key=lambda p: -p["conf"])
    return people, vehicles


def _poly(binmask, max_pts=24):
    """Coarse silhouette polygon, if cv2 is around. Returns [] when it isn't."""
    try:
        import cv2, numpy as np
    except Exception:
        return []
    a = (binmask.detach().cpu().numpy() * 255).astype("uint8")
    cs, _ = cv2.findContours(a, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not cs:
        return []
    c = max(cs, key=cv2.contourArea)
    eps = 0.008 * cv2.arcLength(c, True)
    ap = cv2.approxPolyDP(c, eps, True).reshape(-1, 2)
    if len(ap) > max_pts:
        ap = ap[:: max(1, len(ap) // max_pts)]
    return [[int(x), int(y)] for x, y in ap]


def score(people, vehicles):
    """Descriptive counts plus a confidence proxy. Never a safety verdict.

    `visibility_proxy` is mean detector confidence: when a camera is dark, rainy or
    smeared the detector's own confidence drops. It says "trust these counts this
    much" and nothing else.
    """
    n = len(people)
    veh = sum(vehicles.values())
    conf = sum(p["conf"] for p in people) / n if n else None
    return {"people_count": n, "vehicles": vehicles, "vehicle_count": veh,
            "population": n + veh,
            "visibility_proxy": round(conf, 3) if conf is not None else None}


def rank(rows):
    """Percentile each row against the others in the SAME sweep, per signal.

    Absolute thresholds are meaningless across these cameras: a freeway ramp with 16
    cars is empty, a downtown block with 16 is jammed, because the field of view
    differs. Comparing every camera to every other at the same moment cancels the
    sun, the weather and the day of week.
    """
    def pct(vals, v):
        lower = sum(1 for x in vals if x < v)
        equal = sum(1 for x in vals if x == v)
        return round(100 * (lower + 0.5 * equal) / len(vals))
    if len(rows) < 4:
        for r in rows:
            r["rank"] = None
        return rows
    keys = {"pedestrian_rank": "people_count", "traffic_rank": "vehicle_count",
            "population_rank": "population"}
    for name, field in keys.items():
        vals = [r[field] for r in rows]
        for r in rows:
            r.setdefault("rank", {})[name] = pct(vals, r[field])
    for r in rows:
        r["rank"]["of"] = len(rows)
    return rows


# ---------------------------------------------------------------- drawing

GREEN = (61, 220, 132)
RED = (232, 68, 58)
AMBER = (240, 178, 46)
SKELETON = [("left_shoulder", "right_shoulder"), ("left_shoulder", "left_elbow"),
            ("left_elbow", "left_wrist"), ("right_shoulder", "right_elbow"),
            ("right_elbow", "right_wrist"), ("left_shoulder", "left_hip"),
            ("right_shoulder", "right_hip"), ("left_hip", "right_hip"),
            ("left_hip", "left_knee"), ("left_knee", "left_ankle"),
            ("right_hip", "right_knee"), ("right_knee", "right_ankle")]


def track_color(tid):
    """Stable, bright, distinguishable colour per track id. Deterministic."""
    h = (tid * 47) % 360
    return _hsv(h, 0.72, 1.0)


def _hsv(h, s, v):
    import colorsys
    r, g, b = colorsys.hsv_to_rgb(h / 360.0, s, v)
    return (int(r * 255), int(g * 255), int(b * 255))


def draw(src, people, note, out_path, tracks_by_index=None, show_skeleton=True,
         show_mask=True, trails=None):
    """Annotate one frame. `tracks_by_index[i]` = track id for people[i], if tracked."""
    from PIL import Image, ImageDraw
    with Image.open(src) as im:
        im = im.convert("RGB")
        if show_mask and any("mask_poly" in p and p["mask_poly"] for p in people):
            overlay = Image.new("RGBA", im.size, (0, 0, 0, 0))
            od = ImageDraw.Draw(overlay)
            for i, p in enumerate(people):
                poly = p.get("mask_poly")
                if poly and len(poly) >= 3:
                    c = track_color(tracks_by_index[i]) if tracks_by_index else GREEN
                    od.polygon([tuple(pt) for pt in poly], fill=c + (70,))
            im = Image.alpha_composite(im.convert("RGBA"), overlay).convert("RGB")
        d = ImageDraw.Draw(im)
        if trails:
            for tid, pts in trails.items():
                if len(pts) >= 2:
                    d.line([(x * im.width, y * im.height) for x, y in pts],
                           fill=track_color(tid), width=2)
        for i, p in enumerate(people):
            tid = tracks_by_index[i] if tracks_by_index else None
            col = track_color(tid) if tid is not None else GREEN
            x1, y1, x2, y2 = p["box"]
            d.rectangle([x1, y1, x2, y2], outline=col, width=3)
            d.rectangle([x1 + 1, y1 + 1, x2 - 1, y2 - 1], outline=(16, 40, 26), width=1)
            cx, cy = p["cx"] * im.width, p["cy"] * im.height
            d.ellipse([cx - 4, cy - 4, cx + 4, cy + 4], fill=RED, outline="white", width=1)
            if show_skeleton and p.get("keypoints"):
                kp = p["keypoints"]
                for a, b in SKELETON:
                    pa, pb = kp[KP[a]], kp[KP[b]]
                    if pa[2] >= KP_VISIBLE and pb[2] >= KP_VISIBLE:
                        d.line([pa[0], pa[1], pb[0], pb[1]], fill=AMBER, width=2)
                for x, y, s in kp:
                    if s >= KP_VISIBLE:
                        d.ellipse([x - 2, y - 2, x + 2, y + 2], fill=(255, 255, 255))
            tag = f"#{tid}" if tid is not None else f"{i + 1}"
            if p.get("facing"):
                tag += " " + {"toward_camera": "^", "away_from_camera": "v",
                              "frame_left": "<", "frame_right": ">",
                              "side_on": "|"}.get(p["facing"], "")
            d.text((x1 + 2, max(0, y1 - 11)), f"{tag} {p['conf']:.2f}", fill=col)
        if note:
            d.rectangle([0, 0, min(im.width, 10 + 7 * len(note)), 20], fill=(0, 0, 0))
            d.text((5, 4), note, fill=(255, 255, 255))
        Path(out_path).parent.mkdir(parents=True, exist_ok=True)
        im.save(out_path, quality=88)
