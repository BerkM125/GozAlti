/**
 * Offline place search over the walk graph - the geocoder behind the app's
 * destination bar.
 *
 * Nothing here touches the network. The graph already knows every routable
 * street downtown and every junction where two named streets meet, and those
 * two sets are exactly what a walker types into a destination field: "pike st",
 * "3rd and pine". An external geocoder would add latency, an API key, and an
 * upstream dependency to resolve names the graph can resolve itself - and it
 * could return an address outside the routable bbox, which routing would then
 * refuse anyway.
 *
 * Index shape:
 *   - one entry per named street (point: the midpoint of its longest block,
 *     so it lands on the street itself, never at an averaged point off it)
 *   - one entry per junction where two or more named streets cross,
 *     labelled "A & B", deduplicated per street pair
 *
 * Matching is token-prefix over normalised names, with the usual street
 * abbreviations ("st", "ave", "3rd") expanded so "pike st" and "pike street"
 * are the same query. "&", "and", "at", "@" split a query into cross-street
 * parts, and each part must then match a *different* street of an intersection.
 */

import type { WalkGraph, Edge, LngLat } from "./graph.ts";

export type Place = {
  label: string;
  /** "Street" or "Intersection" - the UI shows it under the label. */
  kind: "street" | "intersection";
  lat: number;
  lon: number;
};

type IndexEntry = Place & {
  /** Normalised tokens per street name: streets have one group, crossings two. */
  groups: string[][];
};

// ---------------------------------------------------------------------------
// Normalisation
// ---------------------------------------------------------------------------

/** Suffixes and directions people type short. Expanded, both forms match. */
const EXPAND: Record<string, string> = {
  st: "street",
  ave: "avenue",
  av: "avenue",
  blvd: "boulevard",
  rd: "road",
  dr: "drive",
  pl: "place",
  ct: "court",
  ln: "lane",
  ter: "terrace",
  pkwy: "parkway",
  hwy: "highway",
  sq: "square",
  aly: "alley",
  n: "north",
  s: "south",
  e: "east",
  w: "west",
  ne: "northeast",
  nw: "northwest",
  se: "southeast",
  sw: "southwest",
  mt: "mount",
};

/** "1st"/"first" etc., so "3rd ave" finds "3rd Avenue" and "third" does too. */
const ORDINALS: Record<string, string> = {
  first: "1st",
  second: "2nd",
  third: "3rd",
  fourth: "4th",
  fifth: "5th",
  sixth: "6th",
  seventh: "7th",
  eighth: "8th",
  ninth: "9th",
  tenth: "10th",
};

function tokens(name: string): string[] {
  return name
    .toLowerCase()
    .replace(/[.,'’]/g, "")
    .split(/[\s/-]+/)
    .filter(Boolean)
    .map((t) => ORDINALS[t] ?? EXPAND[t] ?? t);
}

/** Does every query token prefix-match some token of the name? */
function groupMatches(query: string[], name: string[]): boolean {
  return query.every((q) => name.some((n) => n.startsWith(q)));
}

/**
 * Ranking within the matched set. Exact token hits beat prefixes, matching the
 * front of the name beats matching the tail, and short names beat long ones so
 * "Pike Street" outranks "Pike Street Hillclimb" for "pike".
 */
function score(query: string[], entry: IndexEntry): number {
  const all = entry.groups.flat();
  let s = 0;
  for (const q of query) {
    if (all.includes(q)) s += 3;
    else if (all.some((n) => n.startsWith(q))) s += 1;
    if (all[0]?.startsWith(q)) s += 1;
  }
  return s - all.length * 0.1 - (entry.kind === "street" ? 0 : 0.05);
}

// ---------------------------------------------------------------------------
// Index build
// ---------------------------------------------------------------------------

export class PlaceIndex {
  private entries: IndexEntry[] = [];

  constructor(graph: WalkGraph) {
    this.entries = [...buildStreets(graph), ...buildIntersections(graph)];
  }

  get size(): number {
    return this.entries.length;
  }

  search(rawQuery: string, limit = 8): Place[] {
    const raw = rawQuery.trim();
    if (raw.length < 2) return [];

    // "3rd & pine", "pike and 1st", "2nd at university" - cross-street query.
    const parts = raw
      .toLowerCase()
      .split(/\s+(?:and|at)\s+|\s*[&@]\s*/)
      .map(tokens)
      .filter((p) => p.length > 0);

    const scored: { entry: IndexEntry; s: number }[] = [];
    for (const entry of this.entries) {
      let ok: boolean;
      if (parts.length >= 2) {
        // Each part must match a different street of an intersection.
        ok =
          entry.kind === "intersection" &&
          ((groupMatches(parts[0], entry.groups[0]) && groupMatches(parts[1], entry.groups[1])) ||
            (groupMatches(parts[0], entry.groups[1]) && groupMatches(parts[1], entry.groups[0])));
      } else {
        const q = parts[0] ?? [];
        ok = entry.groups.some((g) => groupMatches(q, g)) || groupMatches(q, entry.groups.flat());
      }
      if (ok) scored.push({ entry, s: score(parts.flat(), entry) });
    }

    scored.sort((a, b) => b.s - a.s || a.entry.label.localeCompare(b.entry.label));
    return scored.slice(0, limit).map(({ entry }) => ({
      label: entry.label,
      kind: entry.kind,
      lat: entry.lat,
      lon: entry.lon,
    }));
  }
}

function midpoint(geometry: LngLat[]): LngLat {
  return geometry[Math.floor(geometry.length / 2)];
}

function buildStreets(graph: WalkGraph): IndexEntry[] {
  const longest = new Map<string, Edge>();
  for (const e of graph.edges.values()) {
    if (e.name === "unnamed street") continue;
    const seen = longest.get(e.name);
    if (!seen || e.length_m > seen.length_m) longest.set(e.name, e);
  }
  return [...longest.entries()].map(([name, edge]) => {
    const [lon, lat] = midpoint(edge.geometry);
    return { label: name, kind: "street" as const, lat, lon, groups: [tokens(name)] };
  });
}

function buildIntersections(graph: WalkGraph): IndexEntry[] {
  const byPair = new Map<string, IndexEntry>();
  for (const [nodeId, [lon, lat]] of graph.nodes) {
    const names = new Set<string>();
    for (const e of graph.adj.get(nodeId) ?? []) {
      if (e.name !== "unnamed street") names.add(e.name);
    }
    if (names.size < 2) continue;
    const list = [...names];
    for (let i = 0; i < list.length; i++) {
      for (let j = i + 1; j < list.length; j++) {
        const key = [list[i], list[j]].sort().join("|");
        if (byPair.has(key)) continue;
        byPair.set(key, {
          label: `${list[i]} & ${list[j]}`,
          kind: "intersection",
          lat,
          lon,
          groups: [tokens(list[i]), tokens(list[j])],
        });
      }
    }
  }
  return [...byPair.values()];
}
