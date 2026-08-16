#!/usr/bin/env python3
"""One call per frame -> people outlines + vehicle counts + a visibility/population score.

  ./scene.py samples/*.jpg                          # default model, overlays -> scene_out/
  ./scene.py samples/*.jpg -m molmo2-8b --api openai
  ./scene.py samples/crowd__*.jpg --json-out scene.jsonl

Uses prompts/scene.txt. Emits one JSON record per frame (see `record()`), close to
the Observation contract in ../../SPEC.md §6.2: normalized cx/cy per person, plus the
outline box so a frontend can draw either.

The score is DESCRIPTIVE, never a safety verdict:
  population = people + vehicles actually seen (raw counts, no weighting of "danger")
  visibility = how much the camera can see right now (model rating x lighting x obstructions)
Both components ship alongside the number so nothing is a black box. A low visibility
score means "do not trust this count", not "this street is bad".
"""
import argparse, base64, json, os, re, sys, time, urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
OLLAMA = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434")
OPENAI = os.environ.get("OPENAI_URL", "http://127.0.0.1:8000/v1")

# How well the camera can see, 0..1. Multiplied together, floored at 0.15.
VIS_RATING = {"clear": 1.0, "partial": 0.6, "poor": 0.25}
VIS_LIGHT = {"daylight": 1.0, "dusk": 0.85, "dark_lit": 0.7, "dark_unlit": 0.4}
VIS_OBSTRUCTION = {"none": 1.0, "glare": 0.8, "rain_on_lens": 0.7, "blur": 0.7,
                   "foliage": 0.85, "vehicle_blocking": 0.85, "construction": 0.9}
VEHICLE_KINDS = ("car", "truck", "bus", "motorcycle", "bicycle")


def convention(model):
    """Coordinate frame per model family — verified on real frames, see NOTES.md."""
    m = model.lower()
    if "gemma" in m or "paligemma" in m: return {"order": "yxyx", "scale": 1000}
    if "qwen3" in m or "molmo" in m:     return {"order": "xyxy", "scale": 1000}
    return {"order": "xyxy", "scale": "px"}


def prep(image, edge=1456):
    """Long edge -> multiple of 28 (Qwen patch size) so pixel-space coords match our file."""
    from PIL import Image
    out = HERE / "prepped" / f"{Path(image).stem}__e{edge}.jpg"
    if out.exists():
        from PIL import Image as I
        with I.open(out) as im: return out, im.size
    out.parent.mkdir(exist_ok=True)
    with Image.open(image) as im:
        im = im.convert("RGB"); s = edge / max(im.size)
        im = im.resize((round(im.width * s / 28) * 28, round(im.height * s / 28) * 28), Image.LANCZOS)
        im.save(out, quality=92)
        return out, im.size


def ask(model, prompt, image, max_tokens, api):
    b64 = base64.b64encode(Path(image).read_bytes()).decode()
    if api == "openai":
        body = {"model": model, "temperature": 0, "max_tokens": max_tokens,
                "response_format": {"type": "json_object"},
                "messages": [{"role": "user", "content": [
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
                    {"type": "text", "text": prompt}]}]}
        url, key = f"{OPENAI}/chat/completions", None
    else:
        body = {"model": model, "prompt": prompt, "images": [b64], "stream": False,
                "format": "json", "keep_alive": "24h", "think": False,
                "options": {"temperature": 0, "num_predict": max_tokens}}
        url, key = f"{OLLAMA}/api/generate", None
    req = urllib.request.Request(url, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json",
                                          "Authorization": "Bearer none"})
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=600) as r:
        out = json.load(r)
    text = (out["choices"][0]["message"].get("content") if api == "openai"
            else (out.get("response") or out.get("thinking") or ""))
    return text, round(time.time() - t0, 2)


def parse(text):
    try:
        return json.loads(text)
    except Exception:
        m = re.search(r"\{.*\}", text, re.S)
        if not m: return None
        try: return json.loads(re.sub(r",\s*([}\]])", r"\1", m.group(0)))
        except Exception: return None


def boxes_px(people, size, model):
    """Model coords -> pixel boxes + normalized cx/cy. Drops degenerate boxes."""
    W, H = size; conv = convention(model); out = []
    for p in people or []:
        bb = p.get("bbox_2d") or p.get("bbox") or p.get("box")
        if not (isinstance(bb, list) and len(bb) == 4): continue
        try: a, b, c, e = [float(v) for v in bb]
        except (TypeError, ValueError): continue
        y1, x1, y2, x2 = (a, b, c, e) if conv["order"] == "yxyx" else (b, a, e, c)
        if max(bb) <= 1:            x1, x2, y1, y2 = x1 * W, x2 * W, y1 * H, y2 * H
        elif conv["scale"] == 1000: x1, x2, y1, y2 = x1 / 1000 * W, x2 / 1000 * W, y1 / 1000 * H, y2 / 1000 * H
        x1, x2 = sorted((max(0.0, x1), min(float(W), x2)))
        y1, y2 = sorted((max(0.0, y1), min(float(H), y2)))
        if x2 - x1 < 3 or y2 - y1 < 3: continue          # a person is never 2 px
        if (x2 - x1) * (y2 - y1) > 0.5 * W * H: continue  # nor half the frame
        out.append({"label": "person", "kind": p.get("kind", "pedestrian"),
                    "box": [round(x1), round(y1), round(x2), round(y2)],
                    "cx": round((x1 + x2) / 2 / W, 4), "cy": round((y1 + y2) / 2 / H, 4)})
    return dedupe(out, W)


def dedupe(dets, W, tol=0.012):
    """Drop repeats: models sometimes emit a ladder of near-identical boxes."""
    keep = []
    for d in dets:
        if any(abs(d["cx"] - k["cx"]) < tol and abs(d["cy"] - k["cy"]) < tol for k in keep):
            continue
        keep.append(d)
    return keep


def score(obs, people_n):
    """Visibility 0..1 and a raw population count. Descriptive only — not a safety verdict."""
    vis = VIS_RATING.get(obs.get("visibility"), 0.6) * VIS_LIGHT.get(obs.get("lighting"), 0.8)
    for o in (obs.get("obstructions") or []):
        vis *= VIS_OBSTRUCTION.get(o, 0.9)
    vis = max(0.15, min(1.0, vis))
    veh = obs.get("vehicles") or {}
    vehicles = {k: int(veh.get(k) or 0) for k in VEHICLE_KINDS}
    vehicle_n = sum(vehicles.values())
    return {
        "people_count": people_n,
        "vehicles": vehicles,
        "vehicle_count": vehicle_n,
        "population": people_n + vehicle_n,      # everything the camera can see moving/standing
        "visibility_score": round(vis, 3),       # confidence in the counts above
        # counts as they'd be if the camera saw perfectly; the honest way to compare
        # a bright corner against a dark one without pretending the dark one is empty
        "population_adjusted": round((people_n + vehicle_n) / vis, 1),
    }


def draw(image, dets, out_path, note=""):
    from PIL import Image, ImageDraw
    with Image.open(image) as im:
        im = im.convert("RGB"); d = ImageDraw.Draw(im)
        for i, det in enumerate(dets, 1):
            x1, y1, x2, y2 = det["box"]
            d.rectangle([x1, y1, x2, y2], outline=(61, 220, 132), width=3)   # outline
            d.rectangle([x1 + 1, y1 + 1, x2 - 1, y2 - 1], outline=(16, 40, 26), width=1)
            cx, cy = det["cx"] * im.width, det["cy"] * im.height
            d.ellipse([cx - 4, cy - 4, cx + 4, cy + 4], fill=(232, 68, 58), outline="white", width=1)
            d.text((x1 + 2, max(0, y1 - 11)), str(i), fill=(61, 220, 132))
        if note:
            d.rectangle([0, 0, min(im.width, 10 + 7 * len(note)), 20], fill=(0, 0, 0))
            d.text((5, 4), note, fill=(255, 255, 255))
        out_path.parent.mkdir(parents=True, exist_ok=True)
        im.save(out_path, quality=88)


def record(src, model, obs, dets, sc, secs):
    return {"frame": str(src), "camera_id": Path(src).name.split("__")[1] if "__" in Path(src).name else None,
            "model": model, "seconds": secs,
            "people_count": sc["people_count"], "detections": dets,
            "vehicles": sc["vehicles"], "vehicle_count": sc["vehicle_count"],
            "population": sc["population"], "population_adjusted": sc["population_adjusted"],
            "visibility": obs.get("visibility"), "visibility_score": sc["visibility_score"],
            "lighting": obs.get("lighting"), "weather_surface": obs.get("weather_surface"),
            "obstructions": obs.get("obstructions") or [], "notable": obs.get("notable", "")}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("images", nargs="+")
    ap.add_argument("-m", "--model", default=os.environ.get("VLM", "qwen3-vl:8b"))
    ap.add_argument("--api", choices=["ollama", "openai"], default=os.environ.get("VLM_API", "ollama"))
    ap.add_argument("-n", "--max-tokens", type=int, default=1200)
    ap.add_argument("--edge", type=int, default=1456)
    ap.add_argument("--out", default="scene_out", help="overlay directory")
    ap.add_argument("--json-out", default=None, help="append records here as jsonl")
    a = ap.parse_args()

    prompt = (HERE / "prompts" / "scene.txt").read_text()
    outdir = HERE / a.out
    tag = "".join(c if c.isalnum() or c in ".-_" else "-" for c in Path(a.model).name)
    rows, fails = [], 0
    for src in a.images:
        img, size = prep(src, a.edge)
        text, secs = ask(a.model, prompt, img, a.max_tokens, a.api)
        obs = parse(text)
        if obs is None:
            fails += 1
            print(f"=== {src}  [{a.model} {secs}s]  PARSE FAIL: {text[:120]}")
            continue
        dets = boxes_px(obs.get("people"), size, a.model)
        sc = score(obs, len(dets))
        note = (f"{sc['people_count']} people · {sc['vehicle_count']} vehicles · "
                f"vis {sc['visibility_score']:.2f} · pop {sc['population']}")
        draw(img, dets, outdir / f"{Path(src).stem}__{tag}.jpg", note)
        rec = record(src, a.model, obs, dets, sc, secs)
        rows.append(rec)
        print(f"=== {Path(src).name}  [{a.model} {secs}s]")
        print(f"    people {sc['people_count']} | vehicles {sc['vehicle_count']} {sc['vehicles']} | "
              f"visibility {obs.get('visibility')} ({sc['visibility_score']:.2f}) {obs.get('lighting')} | "
              f"population {sc['population']} (adj {sc['population_adjusted']})")
        if obs.get("notable"): print(f"    {obs['notable'][:100]}")
        if a.json_out:
            with open(HERE / a.json_out, "a") as fh: fh.write(json.dumps(rec) + "\n")
    if rows:
        n = len(rows)
        print(f"\n{n} frames, {fails} parse fails, {sum(r['seconds'] for r in rows)/n:.2f}s/frame")
        print(f"totals: {sum(r['people_count'] for r in rows)} people, "
              f"{sum(r['vehicle_count'] for r in rows)} vehicles; overlays -> {outdir}")


if __name__ == "__main__":
    main()
