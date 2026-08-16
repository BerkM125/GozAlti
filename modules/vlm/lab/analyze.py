#!/usr/bin/env python3
"""Summarise a bench/ run into a markdown table. Run on the box after bench.sh.

  ./analyze.py                 # all models under bench/
  ./analyze.py qwen2.5vl-7b    # one

Reads bench/<model>/caption.txt and people.txt (ask.py output), plus
samples/mac_reads.json (what the Mac's Qwen2.5-VL said, as a reference — not
ground truth). Prints markdown; paste into MODELS.md.
"""
import json, re, sys, statistics as st
from pathlib import Path

HERE = Path(__file__).resolve().parent
HDR = re.compile(r"^=== (\S+)\s+\[(\S+)\s+([\d.]+)s\s+(\S+) tok")

def parse_blocks(path):
    """ask.py output -> list of {image, model, seconds, tok, json|None, raw}"""
    out, cur = [], None
    for line in path.read_text(errors="replace").splitlines():
        m = HDR.match(line)
        if m:
            if cur: out.append(cur)
            cur = {"image": m.group(1), "model": m.group(2), "seconds": float(m.group(3)),
                   "tok": m.group(4), "raw": []}
        elif cur is not None and not line.startswith("  ->"):
            cur["raw"].append(line)
    if cur: out.append(cur)
    for b in out:
        txt = "\n".join(b["raw"]).strip()
        try:
            b["json"] = json.loads(txt)
        except Exception:
            mm = re.search(r"\{.*\}", txt, re.S)
            try: b["json"] = json.loads(mm.group(0)) if mm else None
            except Exception: b["json"] = None
    return out

def tag_of(image): return Path(image).name.split("__")[0]
def cam_of(image): return Path(image).name.split("__")[1] if "__" in Path(image).name else "?"

def summarize(model_dir, ref):
    cap = parse_blocks(model_dir / "caption.txt") if (model_dir / "caption.txt").exists() else []
    ppl = parse_blocks(model_dir / "people.txt") if (model_dir / "people.txt").exists() else []
    by_img_p = {Path(b["image"]).name: b for b in ppl}
    lines = [f"### {model_dir.name}", ""]
    if cap:
        ok = [b for b in cap if b["json"]]
        secs = [b["seconds"] for b in cap]
        lines.append(f"- caption: parse **{len(ok)}/{len(cap)}**, {st.mean(secs):.2f}s mean, "
                     f"p90 {sorted(secs)[int(0.9*(len(secs)-1))]:.2f}s")
    if ppl:
        ok = [b for b in ppl if b["json"] and isinstance(b["json"].get("people"), list)]
        secs = [b["seconds"] for b in ppl]
        lines.append(f"- people: parse **{len(ok)}/{len(ppl)}**, {st.mean(secs):.2f}s mean, "
                     f"p90 {sorted(secs)[int(0.9*(len(secs)-1))]:.2f}s, "
                     f"{sum(len(b['json']['people']) for b in ok)} boxes total")
    lines += ["", "| tag | cam | mac ppl | caption ppl | boxes | crowd | blocked | constr | emerg | light | cap s | ppl s |",
              "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for b in cap:
        name = Path(b["image"]).name; j = b["json"] or {}
        p = by_img_p.get(name); pj = (p or {}).get("json") or {}
        boxes = len(pj.get("people", [])) if isinstance(pj.get("people"), list) else "—"
        r = ref.get(name, {})
        yn = lambda v: "✓" if v is True else ("·" if v is False else "?")
        lines.append(f"| {tag_of(name)} | {cam_of(name)} | {r.get('people_visible','—')} | "
                     f"{j.get('people_visible','?')} | {boxes} | {j.get('crowding','?')} | "
                     f"{yn(j.get('sidewalk_blocked'))} | {yn(j.get('construction'))} | {yn(j.get('emergency_activity'))} | "
                     f"{j.get('lighting','?')} | {b['seconds']} | {p['seconds'] if p else '—'} |")
    # agreement stats
    if cap and ppl:
        pairs = [(b["json"].get("people_visible"), len(by_img_p[Path(b["image"]).name]["json"]["people"]))
                 for b in cap if b["json"] and Path(b["image"]).name in by_img_p
                 and by_img_p[Path(b["image"]).name]["json"] and isinstance(by_img_p[Path(b["image"]).name]["json"].get("people"), list)]
        if pairs:
            exact = sum(1 for a, c in pairs if a == c); within2 = sum(1 for a, c in pairs if isinstance(a, int) and abs(a - c) <= 2)
            lines.append(f"\ncaption count == boxes on {exact}/{len(pairs)}; within ±2 on {within2}/{len(pairs)}")
    return "\n".join(lines) + "\n"

def main():
    ref = {}
    mr = HERE / "samples" / "mac_reads.json"
    if mr.exists():
        for e in json.loads(mr.read_text()): ref[e["file"]] = e["mac_read"]
    bench = HERE / "bench"
    dirs = [bench / a for a in sys.argv[1:]] if len(sys.argv) > 1 else sorted(p for p in bench.iterdir() if p.is_dir())
    for d in dirs:
        print(summarize(d, ref))

if __name__ == "__main__":
    main()
