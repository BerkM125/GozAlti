#!/usr/bin/env python3
"""Guard: fail if any VLM output asserts safety, danger, or judges people.

`SPEC.md` §7.10 and `CLAUDE.md` make this a hard rule: the VLM describes what it sees
and never issues a safety verdict; only synthesis combines evidence. It is also our
stated differentiator to judges, so a single "pedestrians can cross safely" in the
output contradicts the pitch.

Prompt text alone does not hold. `prompts/insight.txt` already says "Never judge safety,
danger, or crime" and Cosmos-Reason1 still produced "allowing pedestrians to cross
safely" on 4 of 14 frames. So this runs as a mechanical check over the emitted records.

  ./verdict_check.py insight_cosmos.jsonl insight_qwen3.jsonl
  ./verdict_check.py insight_*.jsonl --quiet && echo clean

Exit 0 = clean, 1 = violations found. Wire it into demo prep.
"""
import argparse, json, re, sys
from pathlib import Path

# Words that assert a judgement we are not entitled to make. Split by why they are banned
# so a reviewer can see the reasoning rather than a flat blocklist.
BANNED = {
    "safety verdict": r"\b(safe|safely|safer|safest|unsafe|dangerous|danger|hazardous|risky|risk)\b",
    "threat framing": r"\b(threat|threatening|suspicious|sketchy|shady|menacing|aggressive)\b",
    "crime": r"\b(crime|criminal|illegal|trespass\w*|theft|assault|drug\s*deal\w*)\b",
    "judging people": r"\b(loiter\w*|vagrant\w*|homeless|transient|drunk|intoxicated|deranged)\b",
    "advice": r"\b(should avoid|avoid this|not recommended|stay away|be careful|use caution)\b",
}
# Fields that reach a human. Enum/flag fields are schema-controlled and exempt.
TEXT_FIELDS = ("activity", "walkway_reason", "setting_notes", "notable", "caption", "summary")


def scan_text(text):
    hits = []
    for why, pat in BANNED.items():
        for m in re.finditer(pat, text, re.I):
            hits.append((why, m.group(0), m.start()))
    return hits


def sentence_of(text, pos):
    parts = re.split(r"(?<=[.!?])\s+", text)
    run = 0
    for p in parts:
        if run <= pos < run + len(p) + 1:
            return p.strip()
        run += len(p) + 1
    return text[:160]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("files", nargs="+", help="jsonl emitted by insight.py / video.py")
    ap.add_argument("--quiet", action="store_true")
    ap.add_argument("--fields", default=",".join(TEXT_FIELDS))
    a = ap.parse_args()
    fields = [f.strip() for f in a.fields.split(",") if f.strip()]

    total = violations = 0
    per_model = {}
    for path in a.files:
        p = Path(path)
        if not p.exists():
            print(f"missing: {path}", file=sys.stderr); continue
        for line in p.read_text().splitlines():
            if not line.strip(): continue
            try: r = json.loads(line)
            except Exception: continue
            total += 1
            model = r.get("model", "?")
            per_model.setdefault(model, [0, 0])
            per_model[model][0] += 1
            found = []
            for f in fields:
                v = r.get(f)
                if isinstance(v, str) and v:
                    for why, word, pos in scan_text(v):
                        found.append((f, why, word, sentence_of(v, pos)))
            if found:
                violations += 1
                per_model[model][1] += 1
                if not a.quiet:
                    print(f"\n✗ {r.get('camera_id') or Path(r.get('frame','?')).name}  [{model}]")
                    for f, why, word, sent in found:
                        print(f"    {f}: {why} — \"{word}\"")
                        print(f"      {sent[:150]}")

    print(f"\n{'=' * 60}")
    for model, (n, bad) in sorted(per_model.items()):
        rate = 100 * bad / n if n else 0
        print(f"{model:24} {bad:3d}/{n:<3d} records with a verdict  ({rate:.0f}%)")
    print(f"{'total':24} {violations:3d}/{total:<3d}")
    if violations:
        print("\nFAIL — the VLM asserted a judgement it is not entitled to make.")
        print("Fix by hardening the prompt AND, if it persists, dropping the offending")
        print("sentence before the record leaves this module. Do not ship it.")
    else:
        print("\nclean — no safety/danger/judgement language in user-facing text")
    return 1 if violations else 0


if __name__ == "__main__":
    sys.exit(main())
