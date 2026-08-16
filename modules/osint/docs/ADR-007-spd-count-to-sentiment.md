# ADR-007: SPD counts to sentiment, honestly

Date: 2026-08-15.
Status: accepted.

## Context

SPD Crime Data (SODA dataset `tazs-3rd5`, verified current through 2026-08-14) provides incident rows, not opinions.
The repo's core principle forbids invented safety verdicts and fabricated numbers, yet the §6.3 contract carries a `sentiment` field in [-1, 1].

## Decision

SPD signals use a documented relative-density transform, never a pretend emotion.
The pull is the last 30 days filtered to an explicit street-relevant allow-list of `offense_sub_category` values: AGGRAVATED ASSAULT, ASSAULT OFFENSES, ROBBERY, WEAPON LAW VIOLATION, HOMICIDE (chosen from the live distinct vocabulary; property crime and non-street categories excluded).
Per area, `sentiment = clip(-log2(count / median) / 2, -1, 1)` where `median` is the median count across our tracked areas (floored at 1): the median area scores 0.0, four times the median scores -1.0, a quarter of the median scores +1.0.
The `summary` always states the raw facts ("38 street-relevant SPD offenses (assault/robbery/weapons/homicide) in Belltown in the last 30 days vs tracked-area median of 38"), and the `url` is the literal SODA query with its filters, so a judge can click it and count the same rows.
`observed_at` is the newest offense datetime in the window, converted from SPD's local Pacific timestamps to UTC.

## Consequences

SPD sentiment is relative between our 14 areas, not an absolute danger claim; the summary makes that legible.
Rows with `REDACTED` coordinates and no mapped MCPP are dropped, not guessed.

## Alternatives rejected

Feeding incident rows to the LLM for "sentiment": adds noise to a number that is already a count.
Absolute thresholds (e.g. more than N incidents is -1): arbitrary and indefensible to a judge.
