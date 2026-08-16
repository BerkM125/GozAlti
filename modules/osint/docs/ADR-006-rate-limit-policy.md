# ADR-006: Rate-limit policy

Date: 2026-08-15.
Status: accepted.

## Context

Repo-wide law (SPEC.md §7.5): at most 4 concurrent upstream requests, descriptive User-Agent, and getting the team IP-banned mid-hack is a project-ending failure.

## Decision

Concurrency is 1 by construction: the batch job fetches sequentially with per-source pacing sleeps (`config.PACING_S`: SPD 1 s, Reddit 2 s, news 1 s between requests).
User-Agent is `GozAlti-osint/0.1 (Seattle hackathon research; contact a_dhruv@outlook.com)` on every request via the single client factory.
Raw pulls are cached per UTC day (`data/raw/<source>/<date>.json`); a rerun on the same day fetches nothing.
LLM results are cached per item id (`data/cache/scored.jsonl`); a rerun re-scores nothing.
Backoff on 429/503 honors `Retry-After` and otherwise grows exponentially with jitter, four attempts, then the source fails loudly.

## Consequences

A full pipeline run on warm caches performs zero upstream requests and zero LLM calls; this was verified by running twice and observing zero new records.

## Alternatives rejected

Async fan-out with a semaphore (safe-walk pattern): documented upgrade path, unnecessary at current volumes and strictly riskier against bans.
