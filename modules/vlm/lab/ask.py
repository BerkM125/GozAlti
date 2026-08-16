#!/usr/bin/env python3
"""Feed one image to a VLM on this box (via ollama) and print what comes back.

  ./ask.py IMAGE                          # default model + default prompt (describe)
  ./ask.py IMAGE -m qwen3-vl:8b           # pick a model (ollama list)
  ./ask.py IMAGE -p "How many people?"    # inline prompt
  ./ask.py IMAGE -f prompts/caption.txt   # prompt from file
  ./ask.py IMAGE -f prompts/people.txt --json --draw   # force JSON, draw boxes/dots -> out/
  ./ask.py samples/*.jpg -f prompts/caption.txt --json  # many images, one block each

  ./ask.py IMAGE --api openai -m /home/acer01/models/vlm/Molmo2-8B -p "Point to every person."   # vLLM server

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

def ask_openai(model, prompt, image, json_mode, max_tokens, base=None):
    """Same call against an OpenAI-compatible server (vLLM: docker vllm/vllm-openai, :8000)."""
    base = base or os.environ.get("OPENAI_URL", "http://127.0.0.1:8000/v1")
    b64 = base64.b64encode(Path(image).read_bytes()).decode()
    body = {"model": model, "temperature": 0, "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": [
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
                {"type": "text", "text": prompt}]}]}
    if json_mode: body["response_format"] = {"type": "json_object"}
    req = urllib.request.Request(f"{base}/chat/completions", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json", "Authorization": "Bearer none"})
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=600) as r:
        out = json.load(r)
    text = out["choices"][0]["message"].get("content") or ""
    out["eval_count"] = (out.get("usage") or {}).get("completion_tokens", "?")
    return text, round(time.time() - t0, 2), out

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

def convention(model):
    """How a model family writes coordinates. Verified on real frames (see NOTES.md):
      qwen2.5vl  -> [x1,y1,x2,y2] in pixels of the image it saw   (send --edge 1456 so that == our file)
      qwen3-vl   -> [x1,y1,x2,y2] on a 0..1000 grid
      gemma*     -> [y_min,x_min,y_max,x_max] on a 0..1000 grid  (PaliGemma/Gemma convention; points [y,x])
    Anything ≤1 is treated as a fraction of width/height regardless."""
    m = model.lower()
    if "gemma" in m or "paligemma" in m: return {"order": "yxyx", "scale": 1000}
    if "qwen3" in m:                     return {"order": "xyxy", "scale": 1000}
    return {"order": "xyxy", "scale": "px"}

def draw(image, text, model, outdir):
    """If the reply is JSON with people (bbox_2d or point_2d), draw boxes and/or a dot per person."""
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        print("  (pip install pillow for --draw)"); return None
    try:
        data = json.loads(text)
    except Exception:
        # Molmo's native pointing: <points x1="12.3" y1="45.6" x2=... alt="person">...</points>
        # or <point x="..." y="...">; coordinates are percent of width/height.
        import re
        pts = [(float(x) / 100, float(y) / 100) for x, y in re.findall(r'\bx\d*="([\d.]+)"\s+y\d*="([\d.]+)"', text)]
        if not pts:
            # Molmo 2: <points coords="1  1 032 892  2 070 619 ...">person</points>
            #   = leading frame index, then (idx x y) triplets on a 0..1000 grid (verified on frames)
            for m in re.finditer(r'coords="([\d\s]+)"', text):
                nums = m.group(1).split()
                body = nums[1:] if len(nums) % 3 == 1 else nums
                pts += [(int(body[i + 1]) / 1000, int(body[i + 2]) / 1000) for i in range(0, len(body) - 2, 3)]
        if not pts:
            return None
        data = {"people": [{"point_2d": [x, y]} for x, y in pts]}
    items = data.get("people") or data.get("objects") or data.get("detections") or data.get("points") or []
    im = Image.open(image).convert("RGB"); W, H = im.size; d = ImageDraw.Draw(im); n = 0
    conv = convention(model)
    for it in items:
        if not isinstance(it, dict):
            it = {"point_2d": it} if isinstance(it, list) else {}
        bb = it.get("bbox_2d") or it.get("bbox") or it.get("box")
        pt = it.get("point_2d") or it.get("point")
        if isinstance(bb, list) and len(bb) == 4:
            a, b, c, e = map(float, bb)
            if conv["order"] == "yxyx": y1, x1, y2, x2 = a, b, c, e
            else:                       x1, y1, x2, y2 = a, b, c, e
            if max(bb) <= 1:            x1, x2, y1, y2 = x1*W, x2*W, y1*H, y2*H
            elif conv["scale"] == 1000: x1, x2, y1, y2 = x1/1000*W, x2/1000*W, y1/1000*H, y2/1000*H
            d.rectangle([x1, y1, x2, y2], outline=(242,169,59), width=2)
            cx, cy = (x1+x2)/2, (y1+y2)/2
        elif isinstance(pt, list) and len(pt) == 2:
            a, b = map(float, pt)
            cx, cy = (b, a) if conv["order"] == "yxyx" else (a, b)
            if max(pt) <= 1:            cx, cy = cx*W, cy*H
            elif conv["scale"] == 1000: cx, cy = cx/1000*W, cy/1000*H
        else:
            continue
        d.ellipse([cx-6, cy-6, cx+6, cy+6], fill=(232,68,58), outline="white", width=2); n += 1
    outdir.mkdir(exist_ok=True)
    tag = "".join(c if c.isalnum() or c in ".-_" else "-" for c in Path(model).name)  # ok for paths & names
    out = outdir / f"{Path(image).stem}__{tag}.jpg"
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
    ap.add_argument("--api", choices=["ollama", "openai"], default=os.environ.get("VLM_API", "ollama"),
                    help="ollama (:11434, default) or openai-compatible (vLLM :8000; OPENAI_URL to override)")
    a = ap.parse_args()
    prompt = a.prompt or (Path(a.prompt_file).read_text() if a.prompt_file else
              "Describe what is visible in this traffic camera frame. Count the people you can actually see.")
    for src in a.images:
        img = prep(src, a.edge)
        if a.api == "openai":
            text, secs, raw = ask_openai(a.model, prompt, img, a.json, a.max_tokens)
        else:
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
