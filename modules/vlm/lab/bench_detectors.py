#!/usr/bin/env python3
"""Same protocol for every detector: all frames, native and capped, mean not best-frame.

Written after a bad call: maskrcnn was quoted at 72 ms from its faster frame and
compared against fasterrcnn's 66 ms *sweep average*. Not apples to apples, and the
slow frame was the 1920x1080 one — which matters because a third of SDOT's cameras
are full HD, so the expensive case is the common case.

  docker run --rm --gpus all -v $PWD:/lab -w /lab \
      -v ~/junk/torchcache:/root/.cache/torch \
      --entrypoint python3 vllm/vllm-openai:latest bench_detectors.py

Reports per model: mean/median/p90 ms, split by frame resolution, and the projected
646-camera sweep at each cap.
"""
import argparse, glob, json, statistics as st, time
from pathlib import Path

ARCHS = {
    "fasterrcnn": ("fasterrcnn_resnet50_fpn_v2", "FasterRCNN_ResNet50_FPN_V2_Weights"),
    "maskrcnn": ("maskrcnn_resnet50_fpn_v2", "MaskRCNN_ResNet50_FPN_V2_Weights"),
    "keypointrcnn": ("keypointrcnn_resnet50_fpn", "KeypointRCNN_ResNet50_FPN_Weights"),
}


def bucket(size):
    w, h = size
    px = w * h
    if px >= 1920 * 1080: return "1080p"
    if px >= 1280 * 720: return "720p"
    if px >= 720 * 480: return "480p"
    return "tiny"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--glob", default="samples/*.jpg")
    ap.add_argument("--caps", default="0,1024", help="max long edge; 0 = native")
    ap.add_argument("--repeats", type=int, default=2, help="timed passes per frame, min kept")
    ap.add_argument("--json-out", default="bench_detectors.json")
    a = ap.parse_args()

    import torch
    from torchvision.models import detection as D
    from torchvision.io import read_image
    import torchvision.transforms.v2.functional as F

    files = sorted(glob.glob(a.glob))
    caps = [int(c) for c in a.caps.split(",")]
    sizes = {}
    for f in files:
        img = read_image(f)
        sizes[f] = (img.shape[2], img.shape[1])
    print(f"{len(files)} frames: " + ", ".join(
        f"{b}x{sum(1 for f in files if bucket(sizes[f]) == b)}"
        for b in ("1080p", "720p", "480p", "tiny")))

    results = {}
    for arch, (fn_name, w_name) in ARCHS.items():
        fn = getattr(D, fn_name); w = getattr(D, w_name).DEFAULT
        model = fn(weights=w, box_score_thresh=0.5).eval().cuda()
        tf = w.transforms()
        for cap in caps:
            key = f"{arch}@{'native' if cap == 0 else cap}"
            # warm this (model, cap) pair so compile/alloc isn't charged to frame 1
            img = tf(read_image(files[0])).cuda()
            with torch.no_grad(): model([img])
            torch.cuda.synchronize()
            rows = []
            for f in files:
                raw = read_image(f)
                if cap and max(raw.shape[1], raw.shape[2]) > cap:
                    s = cap / max(raw.shape[1], raw.shape[2])
                    raw = F.resize(raw, [max(1, int(raw.shape[1] * s)), max(1, int(raw.shape[2] * s))])
                img = tf(raw).cuda()
                best = None
                for _ in range(a.repeats):
                    torch.cuda.synchronize(); t0 = time.time()
                    with torch.no_grad(): out = model([img])[0]
                    torch.cuda.synchronize()
                    dt = (time.time() - t0) * 1000
                    best = dt if best is None else min(best, dt)
                rows.append({"frame": f, "bucket": bucket(sizes[f]), "ms": round(best, 1),
                             "people": int((out["labels"] == 1).sum())})
            ms = [r["ms"] for r in rows]
            results[key] = {"rows": rows, "mean": st.mean(ms), "median": st.median(ms),
                            "p90": sorted(ms)[int(0.9 * (len(ms) - 1))],
                            "people": sum(r["people"] for r in rows)}
            print(f"{key:24} mean {st.mean(ms):6.1f}ms  median {st.median(ms):6.1f}  "
                  f"p90 {sorted(ms)[int(0.9*(len(ms)-1))]:6.1f}  people {results[key]['people']:4d}  "
                  f"sweep646 {st.mean(ms)*646/1000:6.1f}s")
        del model; torch.cuda.empty_cache()

    print("\nby resolution bucket (mean ms):")
    buckets = ["1080p", "720p", "480p", "tiny"]
    print(f"{'model@cap':24} " + " ".join(f"{b:>9}" for b in buckets))
    for key, r in results.items():
        cells = []
        for b in buckets:
            v = [x["ms"] for x in r["rows"] if x["bucket"] == b]
            cells.append(f"{st.mean(v):9.1f}" if v else f"{'—':>9}")
        print(f"{key:24} " + " ".join(cells))

    Path(a.json_out).write_text(json.dumps(
        {k: {kk: vv for kk, vv in v.items() if kk != "rows"} | {"rows": v["rows"]}
         for k, v in results.items()}, indent=1))
    print(f"\n-> {a.json_out}")


if __name__ == "__main__":
    main()
