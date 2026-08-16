#!/usr/bin/env python3
"""Feed one image to a VLM on this box (via ollama) and print what comes back.

  ./ask.py IMAGE                          # default model + default prompt (describe)
  ./ask.py IMAGE -m qwen3-vl:8b           # pick a model (ollama list)
  ./ask.py IMAGE -p "How many people?"    # inline prompt
  ./ask.py IMAGE -f prompts/caption.txt   # prompt from file
  ./ask.py IMAGE -f prompts/people.txt --json --draw   # force JSON, draw boxes/dots -> out/
  ./ask.py samples/*.jpg -f prompts/caption.txt --json  # many images, one block each

stdlib only; no venv needed. Output goes to stdout and is appended to log.jsonl.
"""
import argparse, base64, json, sys, time, urllib.request, os
from pathlib import Path

HERE = Path(__file__).resolve().parent
OLLAMA = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434")

def prep(image, edge):
    """Resize so the long edge == edge (multiple of 28 = Qwen patch), cache in prepped/.
    SDOT frames are 720x480; the model's grounding coords are in *its* input space, so
    fixing that space ourselves is what makes boxes line up. Returns path to send."""
    if not edge: return Path(image)
    from PIL import Image
    out = HERE / "prepped" / f"{Path(image).stem}__e{edge}.jpg"
    if not out.exists():
        out.parent.mkdir(exist_ok=True)
        im = Image.open(image).convert("RGB"); s = edge / max(im.size)
        im = im.resize((round(im.width * s / 28) * 28, round(im.height * s / 28) * 28), Image.LANCZOS)
        im.save(out, quality=92)
    return out

def ask(model, prompt, image, json_mode, max_tokens, think=False):
    body = {"model": model, "prompt": prompt, "stream": False, "keep_alive": "24h",
            "images": [base64.b64encode(Path(image).read_bytes()).decode()],
            "options": {"temperature": 0, "num_predict": max_tokens},
            # thinking models (qwen3-vl, gemma4?) put tokens in `thinking` and leave `response`
            # empty unless told not to; we want the answer, not the monologue
            "think": think}
    if json_mode: body["format"] = "json"
    req = urllib.request.Request(f"{OLLAMA}/api/generate", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=600) as r:
        out = json.load(r)
    text = out.get("response", "")
    if not text.strip() and out.get("thinking"):
        text = out["thinking"]  # show *something* rather than a blank block
        out["_note"] = "response empty; showing thinking — pass --think or the model ignored think=false"
    return text, round(time.time() - t0, 2), out

def draw(image, text, model, outdir):
    """If the reply is JSON with people (bbox_2d or point_2d), draw boxes and/or a dot per person."""
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        print("  (pip install pillow for --draw)"); return None
    try:
        data = json.loads(text)
    except Exception:
        return None
    items = data.get("people") or data.get("objects") or data.get("detections") or []
    im = Image.open(image).convert("RGB"); W, H = im.size; d = ImageDraw.Draw(im); n = 0
    norm1000 = "qwen3" in model  # qwen3-vl grounds on a 0..1000 grid; qwen2.5vl in pixels
    for it in items:
        if not isinstance(it, dict):
            it = {"point_2d": it} if isinstance(it, list) else {}
        bb = it.get("bbox_2d") or it.get("bbox") or it.get("box")
        pt = it.get("point_2d") or it.get("point")
        if isinstance(bb, list) and len(bb) == 4:
            x1, y1, x2, y2 = map(float, bb)
            if max(bb) <= 1: x1, x2, y1, y2 = x1*W, x2*W, y1*H, y2*H
            elif norm1000:  x1, x2, y1, y2 = x1/1000*W, x2/1000*W, y1/1000*H, y2/1000*H
            d.rectangle([x1, y1, x2, y2], outline=(242,169,59), width=2)
            cx, cy = (x1+x2)/2, (y1+y2)/2
        elif isinstance(pt, list) and len(pt) == 2:
            cx, cy = map(float, pt)
            if max(pt) <= 1: cx, cy = cx*W, cy*H
            elif norm1000:  cx, cy = cx/1000*W, cy/1000*H
        else:
            continue
        d.ellipse([cx-6, cy-6, cx+6, cy+6], fill=(232,68,58), outline="white", width=2); n += 1
    outdir.mkdir(exist_ok=True)
    out = outdir / f"{Path(image).stem}__{model.replace(':','-')}.jpg"
    im.save(out, quality=88)
    return f"{out} ({n} people marked)"

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("images", nargs="+")
    ap.add_argument("-m", "--model", default=os.environ.get("VLM", "qwen2.5vl:7b"))
    ap.add_argument("-p", "--prompt", default=None)
    ap.add_argument("-f", "--prompt-file", default=None)
    ap.add_argument("--json", action="store_true", help="constrain output to JSON")
    ap.add_argument("--draw", action="store_true", help="draw boxes/dots if reply has bbox_2d")
    ap.add_argument("-n", "--max-tokens", type=int, default=512)
    ap.add_argument("--edge", type=int, default=0, help="resize long edge to N (multiple of 28) before sending; 0 = send file as-is")
    ap.add_argument("--think", action="store_true", help="let thinking models think (slower; default off)")
    a = ap.parse_args()
    prompt = a.prompt or (Path(a.prompt_file).read_text() if a.prompt_file else
              "Describe what is visible in this traffic camera frame. Count the people you can actually see.")
    for src in a.images:
        img = prep(src, a.edge)
        text, secs, raw = ask(a.model, prompt, img, a.json, a.max_tokens, a.think)
        print(f"=== {src}  [{a.model}  {secs}s  {raw.get('eval_count','?')} tok  edge={a.edge or 'orig'}]")
        if raw.get("_note"): print(f"  ({raw['_note']})")
        print(text.strip())
        if a.draw:
            r = draw(img, text, a.model, HERE / "out")
            if r: print(f"  -> {r}")
        with open(HERE / "log.jsonl", "a") as fh:
            fh.write(json.dumps({"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "image": str(src), "sent": str(img), "model": a.model,
                                 "prompt": prompt[:80], "seconds": secs, "response": text}) + "\n")
        print()

if __name__ == "__main__":
    main()
