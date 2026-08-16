# ADR-003: Fetch architecture

Date: 2026-08-15.
Status: accepted.

## Context

Target sources are plain-HTTP (SODA JSON, Reddit OAuth JSON, RSS XML); none require JavaScript rendering.
The owner wanted Playwright available for pages that need a real browser.

## Decision

HTTP-first: one `httpx.Client` factory (`net.make_client`, ported from surukamera's `netboot.make_client` minus the DNS proxy) that forces the descriptive User-Agent, a 20 s timeout, and redirect following; no bare `httpx.get` anywhere.
`net.get_with_retry` adds the retry/backoff the experiments never had: 429/503 honor `Retry-After` (else exponential `2^i * 1.5 s` plus jitter), 5xx retry, other 4xx raise immediately, every retry logged.
Fetching is deliberately synchronous with per-source `time.sleep` pacing; concurrency 1 trivially satisfies the repo-wide cap of 4 concurrent upstream requests.
Playwright lives behind the same `Fetcher` protocol in `fetchers.py` (`fetch_text(url) -> str`), lazily imported, installed only via the `browser` extra, and reserved for JS-heavy article pages that RSS summaries cannot cover.
Playwright is never used to evade a robots.txt or an explicit bot block (see ADR-001).

## Consequences

Fetch time is bounded by pacing, not parallelism, which is fine for a batch job of this size.
The async semaphore pattern from safe-walk's scraper.py is the documented upgrade path if volumes ever grow.

## Alternatives rejected

Playwright for everything: ~1 GB of browser dependencies and slower, flakier fetches for endpoints that serve clean JSON/XML to httpx.
