"""Internal per-area weight rollup — NOT a SPEC.md §6 contract (ADR-005).

Segment-level weighting is synthesis's lane; this file exists so the module owner
can see the weighted collection immediately. Decay halves a signal's influence
every HALF_LIFE_DAYS off observed_at (source-event time); each source's combined
contribution is clamped to ±SOURCE_CAP (safe-walk's LIVE_CAP pattern) so one loud
Reddit thread can't own a neighborhood."""

import json
from datetime import datetime, timezone

from . import areas, config
from .models import iso_utc


def rebuild(signals: list[dict]) -> dict:
    now = datetime.now(timezone.utc)
    per_area: dict[str, dict[str, list[tuple[float, float, str]]]] = {}
    for sig in signals:
        ts = datetime.fromisoformat(sig["observed_at"].replace("Z", "+00:00"))
        decay = 0.5 ** (max(0.0, (now - ts).total_seconds() / 86400) / config.HALF_LIFE_DAYS)
        per_area.setdefault(sig["area"], {}).setdefault(sig["source"], []).append(
            (sig["sentiment"], decay, sig["observed_at"])
        )

    out_areas = {}
    for slug, by_source in sorted(per_area.items()):
        srcs = {}
        contributions = []
        freshest = ""
        for source, entries in sorted(by_source.items()):
            wsum = sum(d for _, d, _ in entries)
            mean = sum(s * d for s, d, _ in entries) / wsum if wsum else 0.0
            srcs[source] = {"w": round(mean, 3), "n": len(entries)}
            contributions.append(max(-config.SOURCE_CAP, min(config.SOURCE_CAP, mean)))
            freshest = max(freshest, max(ts for _, _, ts in entries))
        weight = max(-1.0, min(1.0, sum(contributions) / len(contributions)))
        out_areas[slug] = {
            "weight": round(weight, 3),
            "n_signals": sum(len(e) for e in by_source.values()),
            "by_source": srcs,
            "freshest": freshest,
        }

    doc = {
        "_note": (
            "internal osint artifact; NOT a SPEC.md §6 contract. "
            "Synthesis maps AreaSignals (signals.jsonl) onto segments itself."
        ),
        "generated_at": iso_utc(now),
        "half_life_days": config.HALF_LIFE_DAYS,
        "source_cap": config.SOURCE_CAP,
        "areas": out_areas,
        "coverage": {
            "areas_with_signals": len(out_areas),
            "areas_total": len(areas.AREAS),
            "missing": sorted(a.slug for a in areas.AREAS if a.slug not in out_areas),
        },
    }
    config.AREA_WEIGHTS.write_text(json.dumps(doc, indent=2) + "\n")
    return doc
