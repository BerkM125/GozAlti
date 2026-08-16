"""M2 debug harness: fetch sample frames, render detected segments, VP,
horizon and diagnostics into debug/. Look at these before trusting scores.

Run: python -m scripts.debug_geometry [n_cameras]
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import cv2
import numpy as np

from app import geometry, netboot

ROOT = Path(__file__).resolve().parent.parent
DEBUG = ROOT / "debug"
DEBUG.mkdir(exist_ok=True)


def render(cam: dict, client) -> str:
    data, fetched_at, _ = geometry.fetch_snapshot(cam, client)
    arr = np.frombuffer(data, dtype=np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if img is None:
        return f"{cam['camera_id']}: decode failed"
    scale = 640.0 / img.shape[1]
    img = cv2.resize(img, (640, int(img.shape[0] * scale)))
    h, w = img.shape[:2]
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    gray = cv2.createCLAHE(clipLimit=2.5, tileGridSize=(8, 8)).apply(gray)

    segs = geometry._filter_road_segments(geometry._detect_segments(gray), w, h)
    vp, n_inl, inl_idx = geometry._ransac_vp(segs, w, h)
    inl = set(inl_idx)

    for i, (x1, y1, x2, y2) in enumerate(segs):
        color = (80, 220, 80) if i in inl else (60, 60, 200)
        cv2.line(img, (int(x1), int(y1)), (int(x2), int(y2)), color, 1)
    if vp is not None:
        cv2.circle(img, (int(vp[0]), int(vp[1])), 7, (255, 200, 60), 2)
        cv2.line(img, (0, int(vp[1])), (w, int(vp[1])), (255, 200, 60), 1)

    frame = geometry.analyze_frame(data)
    label = (f"{cam['camera_id']} lines={len(segs)} inl={n_inl} "
             f"conf={frame.get('conf_geom')} slope={frame.get('slope_sign')}")
    cv2.putText(img, label, (6, 16), cv2.FONT_HERSHEY_SIMPLEX, 0.45,
                (255, 255, 255), 1, cv2.LINE_AA)
    out = DEBUG / f"{cam['camera_id']}_{cam['key']}.jpg"
    cv2.imwrite(str(out), img)
    return f"{cam['camera_id']:<10} {cam['key']:<28} lines={len(segs):<4} inliers={n_inl:<3} conf={frame.get('conf_geom')}"


def main() -> None:
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 8
    streets = json.loads((ROOT / "data" / "streets.json").read_text(encoding="utf-8"))
    # Prefer paired cameras (the ones that matter) with streams
    pair_ids = {p["a"] for p in streets["pairs"]} | {p["b"] for p in streets["pairs"]}
    cams = [c for cid, c in streets["cameras"].items() if cid in pair_ids]
    cams.sort(key=lambda c: (not c.get("has_stream"), c["camera_id"]))
    client = netboot.make_client()
    for cam in cams[:n]:
        try:
            print(render(cam, client))
        except Exception as exc:
            print(f"{cam['camera_id']}: ERROR {exc}")
    client.close()


if __name__ == "__main__":
    main()
