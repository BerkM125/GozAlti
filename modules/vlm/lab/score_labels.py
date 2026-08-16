#!/usr/bin/env python3
"""Score model predictions against the human answer key, and say what the key can prove.

Run after labelling:  ./score_labels.py

Reports, per prediction file:
  - the label distribution, because base rate decides which metrics mean anything
  - exact match, and why that number is misleading when one class dominates
  - MISSES: said clear when a human said obstructed. The error that walks someone into
    a construction zone. This is the number the product lives or dies on.
  - FALSE ALARMS: said obstructed when a human said clear. Costs trust, not safety.

If the key has no obstruction positives it says so and refuses to report recall,
rather than printing a hollow accuracy figure.
"""
import json, sys, collections
from pathlib import Path

HERE = Path(__file__).resolve().parent
OBSTRUCTED = {"blocked", "narrowed"}


def load_key(path="labels.jsonl"):
    key = {}
    p = HERE / path
    if not p.exists():
        sys.exit(f"no {path} — label some frames first")
    for line in p.read_text().splitlines():
        if line.strip():
            try:
                r = json.loads(line)
                key[Path(r["frame"]).name] = r["walkway"]   # later lines win
            except Exception:
                pass
    return key


def load_preds(path):
    out = {}
    for line in Path(path).read_text().splitlines():
        if not line.strip():
            continue
        try:
            r = json.loads(line)
        except Exception:
            continue
        f = Path(r.get("frame", "")).name
        if f and r.get("walkway_status"):
            out[f] = (r["walkway_status"], r.get("model", "?"))
    return out


def main():
    key = load_key()
    dist = collections.Counter(key.values())
    n = len(key)
    print(f"ANSWER KEY — {n} frames labelled by hand\n")
    for k, v in dist.most_common():
        print(f"  {k:14} {v:3d}   {100*v/n:5.1f}%")
    pos = sum(v for k, v in dist.items() if k in OBSTRUCTED)
    print(f"\n  obstruction positives (blocked + narrowed): {pos}/{n} = {100*pos/n:.1f}%")

    # base rate governs what this key can and cannot establish
    print("\nWHAT THIS KEY CAN PROVE")
    always_clear = 100 * dist.get("clear", 0) / n
    print(f"  A model that answers 'clear' every single time scores {always_clear:.0f}% exact match.")
    print(f"  So exact match is close to meaningless here; only the rare class matters.")
    if pos == 0:
        print("  MISS RATE: NOT MEASURABLE — the key contains zero obstructed frames.")
        print("  False-alarm rate IS measurable, and is reported below.")
    elif pos < 5:
        print(f"  MISS RATE: only {pos} positive(s). A single disagreement moves recall by "
              f"{100/pos:.0f} points. Treat as directional, not a measurement.")
    else:
        print(f"  MISS RATE: measurable on {pos} positives.")

    files = sorted(HERE.glob("insight*.jsonl"))
    if not files:
        print("\nno insight*.jsonl prediction files to score")
        return

    for pf in files:
        preds = load_preds(pf)
        common = [f for f in key if f in preds]
        if not common:
            print(f"\n{pf.name}: no overlap with the answer key "
                  f"(predictions cover {len(preds)} frames, none labelled)")
            continue
        model = preds[common[0]][1]
        exact = sum(1 for f in common if preds[f][0] == key[f])
        misses = [f for f in common if key[f] in OBSTRUCTED and preds[f][0] == "clear"]
        alarms = [f for f in common if key[f] == "clear" and preds[f][0] in OBSTRUCTED]
        sidewalk_wrong = [f for f in common
                          if (key[f] == "no_sidewalk") != (preds[f][0] == "no_sidewalk")]
        print(f"\n{pf.name}  [{model}]  scored on {len(common)} frames")
        print(f"  exact match          {exact}/{len(common)}  ({100*exact/len(common):.0f}%)")
        keypos = [f for f in common if key[f] in OBSTRUCTED]
        if keypos:
            caught = len(keypos) - len(misses)
            print(f"  MISSES (said clear, was obstructed)   {len(misses)}/{len(keypos)} "
                  f"-> caught {caught}/{len(keypos)}")
            for f in misses[:6]:
                print(f"      {f[:56]}  truth={key[f]}")
        else:
            print(f"  MISSES                                n/a — no obstructed frames in key")
        print(f"  false alarms (said obstructed, was clear)  {len(alarms)}")
        for f in alarms[:6]:
            print(f"      {f[:56]}  said={preds[f][0]}")
        print(f"  no_sidewalk disagreements                  {len(sidewalk_wrong)}")
        for f in sidewalk_wrong[:4]:
            print(f"      {f[:56]}  truth={key[f]} said={preds[f][0]}")


if __name__ == "__main__":
    main()
