#!/usr/bin/env python3
"""CV assists the VLM: find what to look at, crop it, then ask.

The gap this closes. COCO has no class for the things that actually block a
sidewalk — scaffolding, barricades, cones, sandwich boards, planters, debris,
tents, a skip. The detector cannot name them and mostly will not box them. The VLM
*can* name them, but on a 720x480 frame the obstruction is 40 px of a wide street
scene and the model's attention is spread across the whole image.

So: use cheap CV to decide WHERE to look, crop that region at native resolution,
upscale it, and hand the VLM a close-up. The VLM answers a narrow question about a
big clear picture instead of a broad question about a small blurry one.

Three region finders, cheapest first:

  static      background-subtract this frame against the camera's own recent frames.
              Anything that changed and then STOPPED is an obstruction; anything
              that changed and kept moving is traffic. Pure cv2, no model, no GPU.
  unmatched   high-objectness regions the detector proposed but could not confidently
              label — the literal "something is there and COCO has no word for it" set.
  detected    crop around a specific COCO class (default: the walkway strip near people)

  ./assist.py FRAME --history 'frames/CMR-0176/*.jpg' --mode static
  ./assist.py FRAME --mode unmatched -m molmo2-8b --api openai

Molmo is the right model for the follow-up when it is served: its pointing output
means you can ask "point at what is blocking the path" and get coordinates back,
which is a specific answer rather than a paragraph. Ask it specifics, not counts.
"""
import argparse, base64, glob, json, os, sys, time, urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
OLLAMA = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434")
OPENAI = os.environ.get("OPENAI_URL", "http://127.0.0.1:8000/v1")

CROP_PROMPT = """This is a close-up crop from a fixed traffic camera in Seattle, upscaled.
It was selected automatically because something in it changed and then stopped moving,
or because an object detector could not name it.

Identify what is in the crop and whether it affects where a person on foot can walk.
Report only what is visible. Never judge safety, danger, or crime, and never describe
the people in it beyond what they are physically doing.

Answer with JSON and nothing else:
{
  "object": "short noun phrase for the main thing in the crop, or 'nothing notable'",
  "category": "construction" | "barrier" | "vehicle" | "street_furniture" | "debris" | "vegetation" | "people" | "unclear" | "nothing",
  "on_walking_path": true | false | "unclear",
  "effect": "how it changes where someone walks, or empty string",
  "confidence": "high" | "medium" | "low"
}"""


# ---------- region finders -------------------------------------------------------

def regions_static(frame_path, history, min_area=900, max_regions=6):
    """Background-subtract against the camera's own recent frames.

    A median of N past frames is the camera's "empty street". Anything far from that
    median in the current frame is new. We then drop regions that are ALSO different
    between the two most recent history frames — those are moving traffic, not an
    obstruction. What survives appeared and stayed put.
    """
    import cv2, numpy as np
    hist = sorted(history)[-9:]
    if len(hist) < 3:
        return [], "need >=3 history frames for background subtraction"
    cur = cv2.imread(str(frame_path))
    if cur is None: return [], f"cannot read {frame_path}"
    H, W = cur.shape[:2]
    stack = []
    for h in hist:
        im = cv2.imread(str(h))
        if im is None: continue
        if im.shape[:2] != (H, W): im = cv2.resize(im, (W, H))
        stack.append(im)
    if len(stack) < 3: return [], "history frames unreadable or mismatched"
    bg = np.median(np.stack(stack), axis=0).astype(np.uint8)

    def mask_of(a, b):
        d = cv2.absdiff(cv2.cvtColor(a, cv2.COLOR_BGR2GRAY), cv2.cvtColor(b, cv2.COLOR_BGR2GRAY))
        d = cv2.GaussianBlur(d, (5, 5), 0)
        _, m = cv2.threshold(d, 28, 255, cv2.THRESH_BINARY)
        k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7, 7))
        return cv2.morphologyEx(cv2.morphologyEx(m, cv2.MORPH_OPEN, k), cv2.MORPH_CLOSE, k)

    changed = mask_of(cur, bg)
    moving = mask_of(stack[-1], stack[-2])      # frame-to-frame motion = traffic
    static_new = cv2.bitwise_and(changed, cv2.bitwise_not(cv2.dilate(moving, None, iterations=3)))

    out = []
    cnts, _ = cv2.findContours(static_new, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    for c in sorted(cnts, key=cv2.contourArea, reverse=True)[:max_regions]:
        if cv2.contourArea(c) < min_area: continue
        x, y, w, h = cv2.boundingRect(c)
        out.append({"box": [x, y, x + w, y + h], "why": "changed then stopped",
                    "area_px": int(cv2.contourArea(c))})
    return out, f"{len(stack)} history frames, {len(cnts)} contours"


def regions_unmatched(frame_path, conf_lo=0.10, conf_hi=0.45, max_regions=6):
    """Regions the detector proposed but could not confidently name.

    Between the objectness floor and the labelling threshold sits everything COCO has
    no word for. That set is exactly where sidewalk obstructions live.
    """
    sys.path.insert(0, str(HERE))
    import detlib
    det = detlib.load("fasterrcnn", conf_lo, "cuda", None)
    tensor, W, H = detlib.read_tensor(det, frame_path)
    out, _ = detlib.infer(det, tensor)
    people_boxes = [b for b, l, s in zip(out["boxes"].tolist(), out["labels"].tolist(),
                                         out["scores"].tolist()) if l == 1 and s >= 0.5]

    def overlaps(b, others, tol=0.5):
        for o in others:
            ix = max(0, min(b[2], o[2]) - max(b[0], o[0]))
            iy = max(0, min(b[3], o[3]) - max(b[1], o[1]))
            inter = ix * iy
            area = max(1.0, (b[2] - b[0]) * (b[3] - b[1]))
            if inter / area > tol: return True
        return False

    regions = []
    for box, label, sc in zip(out["boxes"].tolist(), out["labels"].tolist(), out["scores"].tolist()):
        if not (conf_lo <= sc < conf_hi): continue
        if overlaps(box, people_boxes): continue        # a blurry person is not a mystery
        x1, y1, x2, y2 = [int(v) for v in box]
        if (x2 - x1) * (y2 - y1) < 600: continue
        regions.append({"box": [x1, y1, x2, y2], "why": f"low-confidence proposal ({sc:.2f})",
                        "area_px": (x2 - x1) * (y2 - y1)})
    regions.sort(key=lambda r: -r["area_px"])
    return regions[:max_regions], f"{len(people_boxes)} confident people excluded"


# ---------- crop + ask ------------------------------------------------------------

def crop(frame_path, box, pad=0.35, min_edge=448):
    """Crop with context padding, then upscale so the VLM sees detail, not pixels."""
    from PIL import Image
    with Image.open(frame_path) as im:
        im = im.convert("RGB"); W, H = im.size
        x1, y1, x2, y2 = box
        px, py = int((x2 - x1) * pad), int((y2 - y1) * pad)
        x1, y1 = max(0, x1 - px), max(0, y1 - py)
        x2, y2 = min(W, x2 + px), min(H, y2 + py)
        c = im.crop((x1, y1, x2, y2))
        if max(c.size) < min_edge:
            s = min_edge / max(c.size)
            c = c.resize((max(1, int(c.width * s)), max(1, int(c.height * s))), Image.LANCZOS)
        return c, (x1, y1, x2, y2)


def ask(model, prompt, image_path, api, max_tokens=300):
    b64 = base64.b64encode(Path(image_path).read_bytes()).decode()
    if api == "openai":
        body = {"model": model, "temperature": 0, "max_tokens": max_tokens,
                "response_format": {"type": "json_object"},
                "messages": [{"role": "user", "content": [
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
                    {"type": "text", "text": prompt}]}]}
        url = f"{OPENAI}/chat/completions"
    else:
        body = {"model": model, "prompt": prompt, "images": [b64], "stream": False,
                "format": "json", "keep_alive": "24h", "think": False,
                "options": {"temperature": 0, "num_predict": max_tokens, "num_ctx": 16384}}
        url = f"{OLLAMA}/api/generate"
    req = urllib.request.Request(url, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json",
                                          "Authorization": "Bearer none"})
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=300) as r:
        o = json.load(r)
    text = (o["choices"][0]["message"].get("content") if api == "openai"
            else (o.get("response") or o.get("thinking") or ""))
    return text, round(time.time() - t0, 2)


def parse(text):
    import re
    try: return json.loads(text)
    except Exception:
        m = re.search(r"\{.*\}", text, re.S)
        if not m: return None
        try: return json.loads(re.sub(r",\s*([}\]])", r"\1", m.group(0)))
        except Exception: return None


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("frame")
    ap.add_argument("--mode", default="static", choices=["static", "unmatched"])
    ap.add_argument("--history", default=None, help="glob of earlier frames from the SAME camera")
    ap.add_argument("-m", "--model", default=os.environ.get("VLM", "qwen3-vl:8b"))
    ap.add_argument("--api", choices=["ollama", "openai"], default=os.environ.get("VLM_API", "ollama"))
    ap.add_argument("--out", default="assist_out")
    ap.add_argument("--max-regions", type=int, default=4)
    ap.add_argument("--json-out", default=None)
    a = ap.parse_args()

    outdir = HERE / a.out / Path(a.frame).stem
    outdir.mkdir(parents=True, exist_ok=True)

    t0 = time.time()
    if a.mode == "static":
        if not a.history: sys.exit("--mode static needs --history 'glob of earlier frames'")
        regions, note = regions_static(a.frame, glob.glob(a.history), max_regions=a.max_regions)
    else:
        regions, note = regions_unmatched(a.frame, max_regions=a.max_regions)
    find_ms = (time.time() - t0) * 1000
    print(f"{Path(a.frame).name}: {len(regions)} region(s) in {find_ms:.0f} ms  ({note})")
    if not regions:
        print("  nothing worth a close-up — the VLM call is skipped entirely")
        return

    records = []
    for i, r in enumerate(regions, 1):
        img, box = crop(a.frame, r["box"])
        cp = outdir / f"crop{i}.jpg"
        img.save(cp, quality=92)
        text, secs = ask(a.model, CROP_PROMPT, cp, a.api)
        ans = parse(text) or {"object": "PARSE FAIL", "raw": text[:120]}
        rec = {"frame": a.frame, "region": i, "box": box, "why": r["why"],
               "crop": str(cp.relative_to(HERE)), "crop_px": img.size,
               "model": a.model, "seconds": secs, **{k: ans.get(k) for k in
               ("object", "category", "on_walking_path", "effect", "confidence")}}
        records.append(rec)
        print(f"  [{i}] {r['why']:34} {img.size[0]}x{img.size[1]}  {secs}s")
        print(f"      {ans.get('object')} ({ans.get('category')}) "
              f"on_path={ans.get('on_walking_path')} conf={ans.get('confidence')}")
        if ans.get("effect"): print(f"      {ans['effect'][:110]}")

    if a.json_out:
        with open(HERE / a.json_out, "a") as fh:
            for r in records: fh.write(json.dumps(r) + "\n")
    blocking = [r for r in records if r.get("on_walking_path") is True]
    print(f"\n{len(records)} crops, {sum(r['seconds'] for r in records):.1f}s VLM total; "
          f"{len(blocking)} on the walking path -> {outdir}")


if __name__ == "__main__":
    main()
