#!/usr/bin/env python3
"""Detector pass: people outlines + vehicle counts, ~65 ms/frame on the GB10.

This is the counting half of the module. A COCO detector does localisation and
counting far better and ~300x faster than asking a VLM to emit coordinates
(measured on our own frames: 65 ms vs 8-27 s, and it finds MORE people). The VLM's
job is the other half — the context a detector has no class for: "scaffolding over
the sidewalk", "street closed, pedestrians diverted", "emergency vehicles on scene".

Runs inside the vLLM container, which already ships torch + torchvision + CUDA:

  docker run --rm --gpus all -v $PWD:/lab -w /lab --entrypoint python3 \
      vllm/vllm-openai:latest detect.py samples/*.jpg --out detect_out

Emits Observation-shaped records (../../SPEC.md §6.2): normalized cx/cy per person
plus the pixel box, vehicle counts, and a descriptive population/visibility score.
Never a safety verdict.
"""
import argparse, glob, json, os, sys, time
from pathlib import Path

HERE = Path(__file__).resolve().parent
VEHICLE_CLASSES = {"car", "truck", "bus", "motorcycle", "bicycle", "train"}
# Vehicle classes we report; `train` is the light rail downtown and worth its own bucket.
REPORT_VEHICLES = ("car", "truck", "bus", "motorcycle", "bicycle", "train")


def load_model(arch, thresh, device):
    import torchvision
    from torchvision.models import detection as D
    table = {
        "fasterrcnn": (D.fasterrcnn_resnet50_fpn_v2, D.FasterRCNN_ResNet50_FPN_V2_Weights),
        "retinanet":  (D.retinanet_resnet50_fpn_v2,  D.RetinaNet_ResNet50_FPN_V2_Weights),
        "fcos":       (D.fcos_resnet50_fpn,          D.FCOS_ResNet50_FPN_Weights),
    }
    fn, wcls = table[arch]
    w = wcls.DEFAULT
    model = fn(weights=w, box_score_thresh=thresh).eval().to(device)
    return model, w.transforms(), w.meta["categories"]


def detect(model, tf, names, path, device, person_thresh, vehicle_thresh):
    import torch
    from torchvision.io import read_image
    img = tf(read_image(str(path))).to(device)
    _, H, W = img.shape
    t0 = time.time()
    with torch.no_grad():
        out = model([img])[0]
    ms = (time.time() - t0) * 1000
    people, vehicles = [], {k: 0 for k in REPORT_VEHICLES}
    for box, label, sc in zip(out["boxes"].tolist(), out["labels"].tolist(), out["scores"].tolist()):
        name = names[label]
        x1, y1, x2, y2 = box
        if name == "person" and sc >= person_thresh:
            people.append({"label": "person", "conf": round(sc, 3),
                           "box": [round(x1), round(y1), round(x2), round(y2)],
                           "cx": round((x1 + x2) / 2 / W, 4), "cy": round((y1 + y2) / 2 / H, 4)})
        elif name in VEHICLE_CLASSES and sc >= vehicle_thresh:
            vehicles[name] = vehicles.get(name, 0) + 1
    people.sort(key=lambda p: -p["conf"])
    return people, vehicles, (W, H), round(ms, 1)


def draw(src, people, note, out_path):
    from PIL import Image, ImageDraw
    with Image.open(src) as im:
        im = im.convert("RGB"); d = ImageDraw.Draw(im)
        for i, p in enumerate(people, 1):
            x1, y1, x2, y2 = p["box"]
            d.rectangle([x1, y1, x2, y2], outline=(61, 220, 132), width=3)
            d.rectangle([x1 + 1, y1 + 1, x2 - 1, y2 - 1], outline=(16, 40, 26), width=1)
            cx, cy = p["cx"] * im.width, p["cy"] * im.height
            d.ellipse([cx - 4, cy - 4, cx + 4, cy + 4], fill=(232, 68, 58), outline="white", width=1)
            d.text((x1 + 2, max(0, y1 - 11)), f"{i} {p['conf']:.2f}", fill=(61, 220, 132))
        if note:
            d.rectangle([0, 0, min(im.width, 10 + 7 * len(note)), 20], fill=(0, 0, 0))
            d.text((5, 4), note, fill=(255, 255, 255))
        out_path.parent.mkdir(parents=True, exist_ok=True)
        im.save(out_path, quality=88)


def score(people, vehicles):
    """Descriptive counts + a crowding-free confidence proxy. Not a safety verdict.

    `visibility_proxy` is mean detector confidence: when a camera is dark, rainy or
    smeared, the detector's own confidence drops, which is a measured signal rather
    than a model opinion. It says "trust these counts this much", nothing more.
    """
    n = len(people)
    veh = sum(vehicles.values())
    conf = sum(p["conf"] for p in people) / n if n else None
    return {"people_count": n, "vehicles": vehicles, "vehicle_count": veh,
            "population": n + veh,
            "visibility_proxy": round(conf, 3) if conf is not None else None}


def rank(rows):
    """Percentile each camera against the others in the SAME sweep, per signal.

    Absolute thresholds are meaningless across these cameras: a freeway ramp with 16
    cars is empty, a downtown block with 16 is jammed, because the field of view is
    different. Comparing every camera to every other camera at the same moment
    cancels the sun, the weather and the day of week, and what is left is the
    difference between this corner and the rest of the city right now. (Same
    reasoning as experiments/safe-walk baseline.rank_now.)
    """
    def pct(vals, v):
        lower = sum(1 for x in vals if x < v)
        equal = sum(1 for x in vals if x == v)
        return round(100 * (lower + 0.5 * equal) / len(vals))
    if len(rows) < 4:            # too few to rank honestly
        for r in rows: r["rank"] = None
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


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("images", nargs="+")
    ap.add_argument("--arch", default="fasterrcnn", choices=["fasterrcnn", "retinanet", "fcos"])
    ap.add_argument("--person-thresh", type=float, default=0.5)
    ap.add_argument("--vehicle-thresh", type=float, default=0.5)
    ap.add_argument("--out", default="detect_out", help="overlay dir; '' to skip drawing")
    ap.add_argument("--json-out", default=None)
    ap.add_argument("--device", default="cuda")
    a = ap.parse_args()

    files = []
    for pat in a.images:
        files += sorted(glob.glob(pat)) if any(c in pat for c in "*?[") else [pat]
    if not files:
        sys.exit("no images matched")

    import torch
    device = a.device if torch.cuda.is_available() else "cpu"
    model, tf, names = load_model(a.arch, min(a.person_thresh, a.vehicle_thresh), device)
    # warm the graph so the first real frame isn't the slow one
    detect(model, tf, names, files[0], device, 1.1, 1.1)

    rows = []
    for f in files:
        people, vehicles, size, ms = detect(model, tf, names, f, device, a.person_thresh, a.vehicle_thresh)
        sc = score(people, vehicles)
        note = (f"{sc['people_count']} people · {sc['vehicle_count']} vehicles · "
                f"pop {sc['population']} · {ms:.0f}ms")
        if a.out:
            draw(f, people, note, HERE / a.out / f"{Path(f).stem}__{a.arch}.jpg")
        rec = {"frame": f, "camera_id": Path(f).name.split("__")[1] if "__" in Path(f).name else None,
               "model": f"torchvision/{a.arch}", "ms": ms, "size": size,
               "detections": people, **sc}
        rows.append(rec)
        print(f"{Path(f).name[:44]:46} {ms:6.0f}ms  people {sc['people_count']:3d}  "
              f"vehicles {sc['vehicle_count']:3d}  pop {sc['population']:3d}  "
              f"conf {sc['visibility_proxy'] if sc['visibility_proxy'] is not None else '—'}")

    rank(rows)
    if any(r.get("rank") for r in rows):
        print("\nranked against the rest of this sweep (percentile, 100 = busiest):")
        print(f"{'camera':24} {'ped':>5} {'traffic':>8} {'pop':>5}")
        for r in sorted(rows, key=lambda r: -r["rank"]["population_rank"]):
            k = r["rank"]
            print(f"{(r['camera_id'] or Path(r['frame']).stem)[:24]:24} "
                  f"p{k['pedestrian_rank']:<4} p{k['traffic_rank']:<7} p{k['population_rank']:<4}")
    if a.json_out:
        with open(HERE / a.json_out, "a") as fh:
            for r in rows: fh.write(json.dumps(r) + "\n")

    n = len(rows)
    tot_ms = sum(r["ms"] for r in rows)
    print(f"\n{n} frames · {tot_ms/n:.0f} ms/frame · {sum(r['people_count'] for r in rows)} people, "
          f"{sum(r['vehicle_count'] for r in rows)} vehicles")
    print(f"projected 646-camera sweep: {tot_ms*646/n/1000:.1f} s serial")


if __name__ == "__main__":
    main()
