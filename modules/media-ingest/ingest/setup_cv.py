"""One-command orchestrator for the local OpenCV CNN stack.

    python -m ingest.setup_cv [--skip-pip]

Does three things, in order, and is safe to re-run (idempotent):
  1. pip-installs this module's requirements.txt into the CURRENT interpreter
     (skip with --skip-pip if the env is already set up)
  2. downloads YOLOv4-tiny (cfg + weights + class names, ~24 MB total, free
     and open source) into data/models/  — one-time, cached forever
  3. smoke-tests a real forward pass on a cached camera frame and prints
     what it detected

Works identically on the dev box and the DGX Spark (aarch64 wheels exist
for everything in requirements.txt). After this, the service's /api/cv/*
endpoints and `ingest.cvdetect` are fully functional with zero network
dependency for inference.
"""
from __future__ import annotations

import subprocess
import sys

from . import config


def _pip_install() -> bool:
    req = config.MODULE_ROOT / "requirements.txt"
    print(f"[setup_cv] pip install -r {req}")
    proc = subprocess.run([sys.executable, "-m", "pip", "install", "-q",
                           "-r", str(req)])
    return proc.returncode == 0


def _find_test_frame() -> bytes | None:
    # newest frame we've already fetched; else surukamera's shipped cache
    for root in (config.FRAMES,
                 config.REPO_ROOT / "experiments" / "surukamera" / "cache" / "snapshots"):
        if not root.exists():
            continue
        jpgs = sorted(root.glob("*/*.jpg"))
        if jpgs:
            return jpgs[-1].read_bytes()
    return None


def main() -> int:
    if "--skip-pip" not in sys.argv:
        if not _pip_install():
            print("[setup_cv] pip install failed — fix the env and re-run")
            return 1

    from . import cvdetect   # after pip so cv2/numpy are importable

    if not cvdetect.ensure_models():
        print("[setup_cv] model download incomplete — re-run when online")
        return 1
    print(f"[setup_cv] models ready in {config.MODELS_DIR}")

    frame = _find_test_frame()
    if frame is None:
        print("[setup_cv] no cached frame to smoke-test on — models are in "
              "place; the first /api/cv call will exercise them")
        return 0
    import time
    t0 = time.monotonic()
    out = cvdetect._detect_in_worker(frame)   # in-process is fine for a smoke test
    ms = round((time.monotonic() - t0) * 1000)
    if "error" in out:
        print(f"[setup_cv] smoke test FAILED: {out['error']}")
        return 1
    labels = [f"{d['label']}({d['conf']})" for d in out["detections"]]
    print(f"[setup_cv] smoke test OK: {out['w']}x{out['h']} frame, {ms} ms, "
          f"{len(labels)} objects: {labels[:8]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
