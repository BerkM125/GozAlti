#!/usr/bin/env python3
"""Detector pass over STILL frames: people outlines + vehicle counts, ~64 ms on the GB10.

This is the counting half of the module. A COCO detector does localisation and
counting far better and ~300x faster than asking a VLM to emit coordinates
(measured on our own frames: 64 ms vs 8-27 s, and it finds MORE people). The VLM's
job is the other half — the context a detector has no class for, and, over video,
the captioning and summarization the See track is built around (see video.py).

All the model, scoring and drawing logic lives in detlib.py, shared with video.py.
Runs inside the vLLM container, which already ships torch + torchvision + CUDA:

  docker run --rm --gpus all -v $PWD:/lab -w /lab --entrypoint python3 \
      vllm/vllm-openai:latest detect.py samples/*.jpg --out detect_out

Emits Observation-shaped records (../../SPEC.md §6.2): normalized cx/cy per person
plus the pixel box, vehicle counts, and a descriptive population/visibility score.
Never a safety verdict.
"""
import argparse, glob, json, sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import detlib


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("images", nargs="+")
    ap.add_argument("--arch", default="fasterrcnn", choices=list(detlib.ARCHS))
    ap.add_argument("--min-size", type=int, default=None,
                    help="override torchvision's internal 800 px resize (the only knob "
                         "that makes input resolution matter)")
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
    det = detlib.load(a.arch, min(a.person_thresh, a.vehicle_thresh), device, a.min_size)
    # warm the graph so the first real frame isn't the slow one
    warm, _, _ = detlib.read_tensor(det, files[0])
    detlib.infer(det, warm)

    rows = []
    for f in files:
        tensor, W, H = detlib.read_tensor(det, f)
        out, ms = detlib.infer(det, tensor)
        people, vehicles = detlib.parse(det, out, W, H, a.person_thresh, a.vehicle_thresh,
                                        mask_polys=det.has_masks)
        sc = detlib.score(people, vehicles)
        note = (f"{sc['people_count']} people · {sc['vehicle_count']} vehicles · "
                f"pop {sc['population']} · {ms:.0f}ms")
        if a.out:
            detlib.draw(f, people, note, HERE / a.out / f"{Path(f).stem}__{a.arch}.jpg")
        rows.append({"frame": f,
                     "camera_id": Path(f).name.split("__")[1] if "__" in Path(f).name else None,
                     "model": f"torchvision/{a.arch}", "min_size": det.min_size,
                     "ms": round(ms, 1), "size": (W, H), "detections": people, **sc})
        print(f"{Path(f).name[:44]:46} {ms:6.0f}ms  people {sc['people_count']:3d}  "
              f"vehicles {sc['vehicle_count']:3d}  pop {sc['population']:3d}  "
              f"conf {sc['visibility_proxy'] if sc['visibility_proxy'] is not None else '—'}")

    detlib.rank(rows)
    if any(r.get("rank") for r in rows):
        print("\nranked against the rest of this sweep (percentile, 100 = busiest):")
        print(f"{'camera':24} {'ped':>5} {'traffic':>8} {'pop':>5}")
        for r in sorted(rows, key=lambda r: -r["rank"]["population_rank"]):
            k = r["rank"]
            print(f"{(r['camera_id'] or Path(r['frame']).stem)[:24]:24} "
                  f"p{k['pedestrian_rank']:<4} p{k['traffic_rank']:<7} p{k['population_rank']:<4}")
    if a.json_out:
        with open(HERE / a.json_out, "a") as fh:
            for r in rows:
                fh.write(json.dumps(r) + "\n")

    n = len(rows)
    tot_ms = sum(r["ms"] for r in rows)
    print(f"\n{n} frames · {tot_ms/n:.0f} ms/frame · {sum(r['people_count'] for r in rows)} people, "
          f"{sum(r['vehicle_count'] for r in rows)} vehicles")
    print(f"projected 646-camera sweep: {tot_ms*646/n/1000:.1f} s serial")


if __name__ == "__main__":
    main()
