"""r/Seattle + r/SeattleWA via the OFFICIAL Reddit OAuth API (ADR-001).

The anonymous public JSON endpoints 403 from this network and reddit robots.txt
disallows crawling, so we do not scrape or browser-spoof around it. Instead: a free
"script" app (https://www.reddit.com/prefs/apps) gives client credentials; put
REDDIT_CLIENT_ID / REDDIT_CLIENT_SECRET in modules/osint/.env and this source turns
on. Without them it skips with a message and the pipeline continues."""

import json
import os
import time
from datetime import datetime, timezone

import httpx

from .. import config, net
from ..models import RawItem, iso_utc

TOKEN_URL = "https://www.reddit.com/api/v1/access_token"
OAUTH_BASE = "https://oauth.reddit.com"


def _token(client: httpx.Client) -> str | None:
    cid = os.getenv("REDDIT_CLIENT_ID")
    secret = os.getenv("REDDIT_CLIENT_SECRET")
    if not cid or not secret:
        print("[reddit] SKIP: no REDDIT_CLIENT_ID/REDDIT_CLIENT_SECRET in .env "
              "(create a free script app at reddit.com/prefs/apps)", flush=True)
        return None
    resp = client.post(TOKEN_URL, auth=(cid, secret), data={"grant_type": "client_credentials"})
    resp.raise_for_status()
    return resp.json()["access_token"]


def pull(client: httpx.Client, limit: int | None = None) -> list[RawItem]:
    per_sub = min(limit or 100, 100)
    cache = config.RAW / "reddit" / f"{datetime.now(timezone.utc):%Y-%m-%d}.json"
    if cache.exists():
        posts = json.loads(cache.read_text())
        print(f"[reddit] cached ({len(posts)} posts)", flush=True)
    else:
        token = _token(client)
        if token is None:
            return []
        headers = {"Authorization": f"Bearer {token}"}
        posts = []
        for sub in config.REDDIT_SUBS:
            resp = net.get_with_retry(
                client,
                f"{OAUTH_BASE}/r/{sub}/search.json",
                headers=headers,
                params={
                    "q": config.REDDIT_QUERY,
                    "restrict_sr": "on",
                    "sort": "new",
                    "t": "year",
                    "limit": per_sub,
                },
            )
            children = resp.json().get("data", {}).get("children", [])
            posts.extend(c["data"] for c in children if c.get("kind") == "t3")
            print(f"[reddit] r/{sub}: {len(children)} posts", flush=True)
            time.sleep(config.PACING_S["reddit"])
        cache.write_text(json.dumps(posts))

    items = [
        RawItem(
            id=f"t3_{p['id']}",
            source="reddit",
            url=f"https://www.reddit.com{p['permalink']}",
            title=p.get("title", ""),
            text=p.get("selftext", "")[:4000],
            published_at=iso_utc(datetime.fromtimestamp(p["created_utc"], tz=timezone.utc)),
        )
        for p in posts
        if p.get("permalink") and p.get("created_utc")
    ]
    return items[:limit] if limit else items
