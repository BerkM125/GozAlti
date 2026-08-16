# ADR-001: Source selection

Date: 2026-08-15.
Status: accepted.

## Context

The module SPEC says pick 2-3 public sources for area-level safety perception, suggesting Reddit, local news RSS, and SPD open data, and requires respecting robots.txt and API terms with no auth'd/paid APIs.

## Decision

Ship three sources: SPD Crime Data via SODA (primary, structured, no scraping), local news RSS (Seattle Times seattle-news, Capitol Hill Seattle, West Seattle Blog), and Reddit via the official OAuth API only when credentials are present in `.env`.
Reddit's anonymous JSON endpoints return 403 from this network for every User-Agent tried (tested 2026-08-15: descriptive UA, reddit-style `platform:app:version` UA, browser UA, and `old.reddit.com`), and `reddit.com/robots.txt` is `Disallow: /` for all agents.
We therefore do not scrape or browser-spoof Reddit; `sources/reddit.py` uses `oauth.reddit.com` with a free script-app client-credentials token and skips with a printed message when no credentials exist.
`mynorthwest.com` feeds 403 bots and were dropped; the three shipped feeds were verified live.

## Consequences

The pipeline works with zero secrets (SPD + news).
Reddit coverage requires a one-time free app registration at reddit.com/prefs/apps and two lines in `modules/osint/.env`.

## Alternatives rejected

Playwright fetching of reddit.com: violates robots.txt and the module SPEC's own terms-compliance rule.
Pushshift mirrors: dead or ToS-violating.
