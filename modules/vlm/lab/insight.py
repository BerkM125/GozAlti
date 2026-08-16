#!/usr/bin/env python3
"""VLM pass: reason about the scene, with the detector's counts injected as context.

The split this module settled on, measured on our own frames:

  detect.py  (COCO detector, 66 ms/frame)  -> WHERE and HOW MANY
      people boxes with confidence, vehicle counts by type, per-sweep percentiles
  insight.py (VLM, ~3-6 s/frame)           -> WHAT IS HAPPENING
      the situation a detector has no class for: scaffolding over the sidewalk,
      a street closure, emergency response, a queue, a freeway ramp with no
      sidewalk at all. Plus the setting a walker should know about.

Asking the VLM to count was the mistake: 8-27 s/frame, and it found fewer people
than the detector (12 vs 15 on CMR-0039) while inventing a box on a traffic signal.
Asking it what is going on is the thing nothing else in the stack can do — and it is
the reasoning layer the See track expects.

  ./insight.py samples/*.jpg                        # VLM alone
  ./insight.py samples/*.jpg --detections detect.jsonl   # with counts injected
  ./insight.py samples/*.jpg -m molmo2-8b --api openai

Merged records (detector counts + VLM reading) are what synthesis consumes.
"""
import argparse, base64, json, os, re, time, urllib.request, glob, sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
OLLAMA = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434")
OPENAI = os.environ.get("OPENAI_URL", "http://127.0.0.1:8000/v1")


def load_detections(path):
    """frame-name -> detector record, from detect.py --json-out."""
    out = {}
    p = HERE / path if not Path(path).is_absolute() else Path(path)
    if not p.exists():
        print(f"(no detections at {p}; running VLM without counts)", file=sys.stderr)
        return out
    for line in p.read_text().splitlines():
        if not line.strip(): continue
        try: r = json.loads(line)
        except Exception: continue
        out[Path(r["frame"]).name] = r          # later lines win: newest run
    return out


def context_block(det):
    """The detector's findings, phrased as given facts the VLM must not recount."""
    if not det:
        return ""
    v = {k: n for k, n in (det.get("vehicles") or {}).items() if n}
    lines = [f"A detector has already counted this frame: {det['people_count']} "
             f"people outside vehicles, {det['vehicle_count']} vehicles"
             + (f" ({', '.join(f'{n} {k}' for k, n in v.items())})" if v else "") + "."]
    rk = det.get("rank") or {}
    if rk.get("population_rank") is not None:
        lines.append(f"Against the other {rk.get('of', '?')} cameras read in this same sweep, "
                     f"this corner is at the {rk['pedestrian_rank']}th percentile for people "
                     f"and the {rk['traffic_rank']}th for vehicles.")
    if det.get("visibility_proxy") is not None:
        lines.append(f"Mean detector confidence here is {det['visibility_proxy']:.2f} "
                     f"(low values mean the view is dark, wet or obstructed).")
    return "\n".join(lines) + "\n\n"


def ask(model, prompt, image, max_tokens, api):
    b64 = base64.b64encode(Path(image).read_bytes()).decode()
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
                "options": {"temperature": 0, "num_predict": max_tokens}}
        url = f"{OLLAMA}/api/generate"
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


# VLM events -> the module's flag enum (modules/vlm/SPEC.md)
EVENT_FLAGS = {"construction": "construction", "road_closure": "road_closure",
               "emergency_response": "emergency_response", "crowd": "crowd",
               "queue": "queue", "loading": "loading",
               "stalled_vehicle": "stalled_vehicle", "transit_stop": "transit_stop"}
WALKWAY_FLAGS = {"blocked": "blocked_sidewalk", "narrowed": "narrowed_sidewalk",
                 "no_sidewalk": "no_sidewalk"}


def merge(frame, det, ins, secs, model):
    """Detector counts + VLM reading -> one Observation-shaped record."""
    flags = []
    for e in (ins.get("events") or []):
        if e in EVENT_FLAGS and EVENT_FLAGS[e] not in flags: flags.append(EVENT_FLAGS[e])
    w = WALKWAY_FLAGS.get(ins.get("walkway_status"))
    if w: flags.append(w)
    if det and det.get("people_count") == 0: flags.append("no_people")
    return {
        "frame": frame,
        "camera_id": (det or {}).get("camera_id") or (Path(frame).name.split("__")[1]
                                                      if "__" in Path(frame).name else None),
        "model": model, "vlm_seconds": secs, "detector_ms": (det or {}).get("ms"),
        # counts: detector's, never the VLM's
        "people_count": (det or {}).get("people_count"),
        "detections": (det or {}).get("detections", []),
        "vehicles": (det or {}).get("vehicles"), "vehicle_count": (det or {}).get("vehicle_count"),
        "population": (det or {}).get("population"), "rank": (det or {}).get("rank"),
        # reading: VLM's, never a count
        "scene": ins.get("scene"), "activity": ins.get("activity", ""),
        "walkway_status": ins.get("walkway_status"), "walkway_reason": ins.get("walkway_reason", ""),
        "events": ins.get("events") or [], "setting_notes": ins.get("setting_notes", ""),
        "vlm_confidence": ins.get("confidence"), "flags": flags,
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("images", nargs="+")
    ap.add_argument("-m", "--model", default=os.environ.get("VLM", "qwen3-vl:8b"))
    ap.add_argument("--api", choices=["ollama", "openai"], default=os.environ.get("VLM_API", "ollama"))
    ap.add_argument("--detections", default="detect.jsonl", help="detect.py --json-out file; '' to skip")
    ap.add_argument("-n", "--max-tokens", type=int, default=400)
    ap.add_argument("--json-out", default=None)
    a = ap.parse_args()

    files = []
    for pat in a.images:
        files += sorted(glob.glob(pat)) if any(c in pat for c in "*?[") else [pat]
    if not files: sys.exit("no images matched")

    base = (HERE / "prompts" / "insight.txt").read_text()
    dets = load_detections(a.detections) if a.detections else {}
    rows, fails = [], 0
    for f in files:
        det = dets.get(Path(f).name)
        prompt = context_block(det) + base
        text, secs = ask(a.model, prompt, f, a.max_tokens, a.api)
        ins = parse(text)
        if ins is None:
            fails += 1
            print(f"=== {Path(f).name}  PARSE FAIL: {text[:100]}")
            continue
        rec = merge(f, det, ins, secs, a.model)
        rows.append(rec)
        head = f"=== {Path(f).name[:42]:44} [{secs}s]"
        if det: head += f"  {det['people_count']}p/{det['vehicle_count']}v"
        print(head)
        print(f"    {ins.get('scene','?')} · walkway {ins.get('walkway_status','?')}"
              + (f" ({ins['walkway_reason']})" if ins.get("walkway_reason") else "")
              + (f" · {', '.join(rec['events'])}" if rec["events"] and rec["events"] != ["none"] else ""))
        if ins.get("activity"): print(f"    {ins['activity']}")
        if ins.get("setting_notes"): print(f"    setting: {ins['setting_notes']}")
        if a.json_out:
            with open(HERE / a.json_out, "a") as fh: fh.write(json.dumps(rec) + "\n")

    if rows:
        n = len(rows)
        print(f"\n{n} frames, {fails} parse fails, {sum(r['vlm_seconds'] for r in rows)/n:.2f}s/frame VLM")
        flagged = [r for r in rows if r["flags"] and r["flags"] != ["no_people"]]
        print(f"{len(flagged)} frames carry a flag: "
              + ", ".join(sorted({fl for r in flagged for fl in r['flags'] if fl != 'no_people'})))


if __name__ == "__main__":
    main()
