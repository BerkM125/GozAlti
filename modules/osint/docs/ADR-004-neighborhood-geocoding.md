# ADR-004: Neighborhood geocoding

Date: 2026-08-15.
Status: accepted.

## Context

Signals must land on areas with centroids (SPEC.md §6.3).
The module SPEC warns that fuzzy street-name geocoding is a rabbit hole, and exploration confirmed no reusable text-geocoding exists anywhere in the repo.

## Decision

A hand-set registry of 14 neighborhoods in `areas.py` (slug, name, centroid, text aliases, SPD MCPP names), covering the demo corridor plus the areas people actually post about.
Centroids seeded from safe-walk's `api.PLACES` and hand-checked.
Three matchers, in order of trust:
`match_mcpp` maps SPD's `neighborhood` field (MCPP names, live vocabulary verified 2026-08-15 via a `$select=distinct neighborhood` probe) through an exact dictionary.
`match_point` snaps a lat/lon to the nearest centroid within 1.2 km (SPD blurs coordinates to the hundred-block, which is noise at this granularity).
`match_text` does word-boundary alias matching over title+body for Reddit/news, capped at 2 areas per item.

## Consequences

Neighborhood granularity only; street-level mentions inside a known neighborhood resolve to that neighborhood.
Items mentioning no registered area are dropped (counted and printed, never guessed).
Lower Queen Anne shares SPD's single QUEEN ANNE MCPP with Queen Anne, so it only receives text signals; it is the one expected coverage gap.

## Alternatives rejected

Nominatim/geopy text geocoding: heavy, slow, and wrong often enough to poison evidence.
SDOT's 13 operational districts (surukamera): wrong vocabulary for how people write ("East" vs "Capitol Hill").
