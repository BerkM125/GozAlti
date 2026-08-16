"""CLI: osint run | pull | score | aggregate | status"""

import argparse
import json
import sys

from pydantic import ValidationError

from . import aggregate, config, pipeline
from .models import AreaSignal

ALL_SOURCES = ["spd", "reddit", "news"]


def main() -> None:
    ap = argparse.ArgumentParser(prog="osint", description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_run = sub.add_parser("run", help="full pipeline: all sources -> signals + weights")
    p_run.add_argument("--limit", type=int, default=None, help="cap items per source")
    p_run.add_argument("--source", choices=ALL_SOURCES, action="append", default=None)

    p_pull = sub.add_parser("pull", help="one source only")
    p_pull.add_argument("--source", choices=ALL_SOURCES, required=True)
    p_pull.add_argument("--limit", type=int, default=None)

    sub.add_parser("aggregate", help="rebuild area_weights.json from signals.latest.json")
    sub.add_parser("status", help="validate signals.jsonl, print per-area/source counts")

    args = ap.parse_args()
    if args.cmd == "run":
        pipeline.run(args.source or ALL_SOURCES, args.limit)
    elif args.cmd == "pull":
        pipeline.run([args.source], args.limit)
    elif args.cmd == "aggregate":
        if not config.SIGNALS_LATEST.exists():
            sys.exit("no signals.latest.json yet — run `osint run` first")
        doc = aggregate.rebuild(json.loads(config.SIGNALS_LATEST.read_text()))
        print(json.dumps(doc["areas"], indent=2))
    elif args.cmd == "status":
        status()


def status() -> None:
    records = pipeline.read_jsonl(config.SIGNALS_JSONL)
    bad = 0
    counts: dict[tuple[str, str], int] = {}
    for rec in records:
        try:
            AreaSignal(**rec)
        except ValidationError as exc:
            bad += 1
            print(f"INVALID: {exc.errors()[0]['msg']} in {json.dumps(rec)[:120]}", flush=True)
            continue
        counts[(rec["area"], rec["source"])] = counts.get((rec["area"], rec["source"]), 0) + 1
    print(f"{len(records)} records in {config.SIGNALS_JSONL} ({bad} invalid)")
    for (area, source), n in sorted(counts.items()):
        print(f"  {area:24s} {source:8s} {n}")
    if config.AREA_WEIGHTS.exists():
        cov = json.loads(config.AREA_WEIGHTS.read_text())["coverage"]
        print(f"coverage: {cov['areas_with_signals']}/{cov['areas_total']} areas; missing: {', '.join(cov['missing']) or '-'}")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
