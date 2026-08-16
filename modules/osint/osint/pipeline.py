"""Orchestration: fetch -> normalize -> dedup -> geotag -> keyword gate -> LLM score
-> emit AreaSignal JSONL (+ latest snapshot) -> rebuild aggregate weights."""

import json
import time
from pathlib import Path

from . import aggregate, areas, config, llm, net
from .models import AreaSignal, Centroid, RawItem


def read_jsonl(path: Path) -> list[dict]:
    """Corruption-tolerant line reader (house convention: skip bad lines, never die)."""
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def append_jsonl(path: Path, records: list[dict]) -> None:
    with path.open("a", encoding="utf-8") as fh:
        for rec in records:
            fh.write(json.dumps(rec) + "\n")


def emit(new_signals: list[AreaSignal]) -> int:
    """Validate, append to signals.jsonl, rewrite signals.latest.json (dedup by source+url+area,
    newest observed_at wins). Returns the number of genuinely new records."""
    existing = read_jsonl(config.SIGNALS_JSONL)
    seen = {(s["source"], s["url"], s["area"], s["observed_at"]) for s in existing}
    fresh = []
    for sig in new_signals:
        rec = sig.model_dump()
        if (rec["source"], rec["url"], rec["area"], rec["observed_at"]) not in seen:
            fresh.append(rec)
    append_jsonl(config.SIGNALS_JSONL, fresh)

    latest: dict[tuple, dict] = {}
    for rec in existing + fresh:
        key = (rec["source"], rec["url"], rec["area"])
        if key not in latest or rec["observed_at"] > latest[key]["observed_at"]:
            latest[key] = rec
    snapshot = sorted(latest.values(), key=lambda r: (r["area"], r["source"], r["url"]))
    config.SIGNALS_LATEST.write_text(json.dumps(snapshot, indent=2) + "\n")
    aggregate.rebuild(snapshot)
    return len(fresh)


def _gate(item: RawItem) -> bool:
    text = f"{item.title} {item.text}".lower()
    return any(kw in text for kw in config.SAFETY_LEXICON)


def score_items(items: list[RawItem]) -> list[AreaSignal]:
    """Geotag + keyword gate + LLM score. Items with no area or no safety keyword are free;
    only survivors cost an LLM call (cached, so reruns cost zero)."""
    signals: list[AreaSignal] = []
    n_gated = n_untagged = 0
    for item in items:
        slugs = areas.match_text(f"{item.title} {item.text}")
        if not slugs:
            n_untagged += 1
            continue
        if not _gate(item):
            n_gated += 1
            continue
        result = llm.score(item, [areas.BY_SLUG[s].name for s in slugs])
        if result is None or not result.relevant:
            continue
        for slug in slugs:
            a = areas.BY_SLUG[slug]
            signals.append(
                AreaSignal(
                    area=slug,
                    centroid=Centroid(lat=a.lat, lon=a.lon),
                    source=item.source,
                    url=item.url,
                    observed_at=item.published_at,
                    sentiment=result.sentiment,
                    summary=result.summary,
                )
            )
    print(
        f"[score] {len(items)} items: {n_untagged} no-area, {n_gated} gated, "
        f"{len(signals)} signals",
        flush=True,
    )
    return signals


def run(sources: list[str], limit: int | None = None) -> None:
    from .sources import news, reddit, spd

    total = 0
    with net.make_client() as client:
        if "spd" in sources:
            total += emit(spd.signals(client, limit))
            time.sleep(config.PACING_S["spd"])
        if "reddit" in sources:
            total += emit(score_items(reddit.pull(client, limit)))
        if "news" in sources:
            total += emit(score_items(news.pull(client, limit)))
    print(f"[run] {total} new signals -> {config.SIGNALS_JSONL}", flush=True)
