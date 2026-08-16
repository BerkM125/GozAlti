"""M3 report: compute geometry for every paired camera and print every
candidate pair with its classification, score and reason.

Run: python -m scripts.run_pairs [limit]
Writes data/pair_report.json for the server to serve as a warm cache.
"""
from __future__ import annotations

import json
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from pathlib import Path

from app import geometry, netboot, pairs

ROOT = Path(__file__).resolve().parent.parent


def main() -> None:
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else 10_000
    streets = json.loads((ROOT / "data" / "streets.json").read_text(encoding="utf-8"))
    cams = streets["cameras"]
    cand = streets["pairs"][:limit]

    need = sorted({p["a"] for p in cand} | {p["b"] for p in cand})
    client = netboot.make_client()
    geoms: dict[str, object] = {}

    def compute(cid: str) -> None:
        try:
            geoms[cid] = geometry.compute_view_geometry(cams[cid], client)
        except Exception as exc:
            print(f"  geometry failed {cid}: {exc}")

    with ThreadPoolExecutor(max_workers=4) as pool:  # fetch discipline
        list(pool.map(compute, need))
    client.close()

    rows = []
    for p in cand:
        gA, gB = geoms.get(p["a"]), geoms.get(p["b"])
        if gA is None or gB is None:
            continue
        d = pairs.classify_pair(cams[p["a"]], cams[p["b"]], gA, gB, p["gap_m"])
        rows.append({**p, "decision": asdict(d)})
        print(f"{p['street'][:30]:<31} {p['a']}->{p['b']} "
              f"{d.layout:<19} score={d.score:<5} {d.reason[:90]}")

    out = {
        "pairs": rows,
        "geometries": {cid: asdict(g) for cid, g in geoms.items()},
    }
    (ROOT / "data" / "pair_report.json").write_text(json.dumps(out, indent=1))
    n_stack = sum(1 for r in rows if r["decision"]["layout"] == "STACKED_CONTINUITY")
    print(f"\n{len(rows)} pairs, {n_stack} stackable")


if __name__ == "__main__":
    main()
