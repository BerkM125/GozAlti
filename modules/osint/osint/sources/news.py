"""Local news via RSS (feedparser). Summaries only by default; full article text is the
optional Playwright path in fetchers.py — RSS summaries are usually enough to score."""

import hashlib
import json
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

import httpx

from .. import config, net
from ..models import RawItem, iso_utc


def pull(client: httpx.Client, limit: int | None = None) -> list[RawItem]:
    import feedparser

    cache = config.RAW / "news" / f"{datetime.now(timezone.utc):%Y-%m-%d}.json"
    if cache.exists():
        entries = json.loads(cache.read_text())
        print(f"[news] cached ({len(entries)} entries)", flush=True)
    else:
        entries = []
        for feed_url in config.NEWS_FEEDS:
            try:
                resp = net.get_with_retry(client, feed_url)
            except httpx.HTTPError as exc:
                print(f"[news] SKIP {feed_url}: {exc!r}", flush=True)
                continue
            parsed = feedparser.parse(resp.text)
            for e in parsed.entries:
                entries.append(
                    {
                        "link": e.get("link", ""),
                        "title": e.get("title", ""),
                        "summary": e.get("summary", ""),
                        "published": e.get("published", "") or e.get("updated", ""),
                    }
                )
            print(f"[news] {feed_url}: {len(parsed.entries)} entries", flush=True)
            time.sleep(config.PACING_S["news"])
        cache.write_text(json.dumps(entries))

    items = []
    for e in entries:
        if not e["link"]:
            continue
        try:
            ts = iso_utc(parsedate_to_datetime(e["published"]))
        except (ValueError, TypeError):
            continue  # no honest source-event time -> skip rather than fake one
        items.append(
            RawItem(
                id=hashlib.sha1(e["link"].encode()).hexdigest()[:16],
                source="news",
                url=e["link"],
                title=e["title"],
                text=e["summary"][:4000],
                published_at=ts,
            )
        )
    return items[:limit] if limit else items
