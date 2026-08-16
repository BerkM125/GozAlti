# ADR-005: Output shapes and aggregate weights

Date: 2026-08-15.
Status: accepted.

## Context

The contract output is `AreaSignal` (SPEC.md §6.3); per-segment path weighting is explicitly synthesis's lane.
The owner also wants an immediately visible "weighted collection" of areas without waiting on synthesis.

## Decision

Two artifacts.
`data/signals.jsonl` is the append-only contract stream, validated record-by-record with pydantic (`models.AreaSignal`) before writing; `data/signals.latest.json` is the deduped snapshot (key: source+url+area, newest `observed_at` wins) that synthesis should prefer.
`data/area_weights.json` is an internal rollup and says so in an embedded `_note`: per-signal exponential decay with a 14-day half-life off `observed_at` (source-event time), a decay-weighted mean per source, and each source's contribution clamped to ±0.35 before averaging - the same capped-influence idea as safe-walk's `LIVE_CAP`, so one loud source cannot own a neighborhood.
The rollup is sparse (only areas with signals) and carries a coverage block naming the missing areas.

## Consequences

Synthesis consumes files, not an endpoint; no port is claimed in SPEC.md §6.8.
Deleting `data/` and rerunning rebuilds everything (raw pulls re-fetch, scored cache re-scores).

## Alternatives rejected

Emitting per-segment weights from osint: crosses the synthesis lane and would need a contract change.
A REST endpoint: needless moving part for a file-shaped handoff during a 48-hour hack.
