# modules/osint — Non-Media Scraping (model's input, optional)

**Owner:** Dhruv · **Effort budget:** 3 h · **Optional — cut first if time runs out**

Read `../../SPEC.md` first. Lane: this directory only.

## Scope

Area-level safety sentiment from public non-media sources, emitted as
`AreaSignal` (§6.3) records for synthesis:

- **Sources** (pick 2–3, don't boil the ocean): Reddit (r/Seattle, r/SeattleWA
  via the public JSON API), local news RSS, Seattle PD / City of Seattle open
  data (SPD Crime Data on the open data portal is structured and reliable —
  start there; it needs no scraping, just SODA queries).
- **Geocoding to areas**: map mentions to a small fixed neighborhood list
  (Belltown, Pioneer Square, Capitol Hill, SLU, Downtown, U-District, …) with
  hand-set centroids. Fuzzy street-name matching is a rabbit hole — neighborhood
  granularity is enough for the demo.
- **Sentiment/summarization**: use an LLM on the Spark (or NVIDIA Build
  endpoints) to score sentiment in [-1, 1] and write a one-line evidence
  summary per signal. The summary is what users see — make it concrete
  ("two posts describing X near Y last week"), never vague vibes.
- Batch job + JSONL output (`data/signals.jsonl`) is fine; synthesis reads the
  file or a `GET /signals?area=` endpoint — your call, document it here.

**Out of scope:** imagery (media-ingest), per-segment scoring (synthesis maps
area signals onto segments), anything requiring auth'd/paid APIs.

## Contracts

Produces `AreaSignal` (§6.3). Every record keeps its `url` — evidence must be
checkable by a judge.

## Definition of done (demo)

At least the demo-route neighborhoods have real signals with working URLs, and
they visibly show up in segment evidence popovers via synthesis.

## Practices

- Respect robots.txt and API terms; descriptive User-Agent; cache raw pulls so
  reruns don't re-fetch.
- Timestamp everything with the *source event* time, not scrape time; synthesis
  decays old signals.
- No doxxing content: signals describe places and events, never identify
  individuals from posts.
