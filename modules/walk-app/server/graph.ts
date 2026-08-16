/**
 * Pedestrian street graph, built from the Overpass dump that surukamera already
 * ships (experiments/surukamera/data/osm_ways.json, 23,090 named ways).
 *
 * Why this source and not safe-walk's: OSM ways carry real, shared node ids, so
 * junctions are exact rather than inferred by rounding coordinates to a 12 m
 * bucket. The dump is committed, so the graph builds offline with no SDOT fetch.
 *
 * Nodes are junctions. Edges are the stretch of one way between two junctions -
 * a city block - which is the unit the evidence sheet talks about ("Pike St,
 * 1st Ave -> 2nd Ave").
 */

import { readFileSync, writeFileSync, existsSync, mkdirSync } from "node:fs";
import { dirname, join } from "node:path";

export type LngLat = [number, number];

/** Raw shape of an element in the Overpass dump. */
type OsmWay = {
  type: "way";
  id: number;
  bounds: { minlat: number; minlon: number; maxlat: number; maxlon: number };
  nodes: number[];
  geometry: { lat: number; lon: number }[];
  tags: Record<string, string>;
};

export type Edge = {
  /** Stable across rebuilds: derived from OSM ids, never from array position. */
  segment_id: string;
  way_id: number;
  a: number; // junction node id
  b: number; // junction node id
  name: string;
  /** Full block geometry in GeoJSON order, [lon, lat]. */
  geometry: LngLat[];
  length_m: number;
  risk: number;
  /** Per-component risk with the OSM tag each component was read from. */
  evidence: RiskEvidence[];
  tags: Record<string, string>;
};

export type RiskEvidence = {
  type: "sidewalk" | "lighting" | "traffic" | "crossing";
  score: number;
  detail: string;
  /** The OSM tag this was read from, or null when nothing was tagged. */
  ref: string | null;
  inferred: boolean;
};

export type WalkGraph = {
  nodes: Map<number, LngLat>;
  adj: Map<number, Edge[]>;
  edges: Map<string, Edge>;
  bbox: [number, number, number, number];
};

// ---------------------------------------------------------------------------
// Geometry
// ---------------------------------------------------------------------------

const R_EARTH = 6_371_008.8;

export function haversine(a: LngLat, b: LngLat): number {
  const [lon1, lat1] = a;
  const [lon2, lat2] = b;
  const p1 = (lat1 * Math.PI) / 180;
  const p2 = (lat2 * Math.PI) / 180;
  const dp = ((lat2 - lat1) * Math.PI) / 180;
  const dl = ((lon2 - lon1) * Math.PI) / 180;
  const h =
    Math.sin(dp / 2) ** 2 + Math.cos(p1) * Math.cos(p2) * Math.sin(dl / 2) ** 2;
  return 2 * R_EARTH * Math.asin(Math.sqrt(h));
}

function polylineLength(coords: LngLat[]): number {
  let m = 0;
  for (let i = 1; i < coords.length; i++) m += haversine(coords[i - 1], coords[i]);
  return m;
}

// ---------------------------------------------------------------------------
// Walkability
// ---------------------------------------------------------------------------

/**
 * Freeways are never walkable. Trunk roads sometimes are, so they are admitted
 * only when OSM positively says a sidewalk exists - we do not guess our way onto
 * a highway shoulder.
 */
const NEVER_WALKABLE = new Set(["motorway", "motorway_link"]);
const NEEDS_SIDEWALK_PROOF = new Set(["trunk", "trunk_link"]);

function sidewalkTag(tags: Record<string, string>): { value: string; ref: string } | null {
  for (const key of ["sidewalk", "sidewalk:both", "sidewalk:left", "sidewalk:right"]) {
    const v = tags[key];
    if (v !== undefined) return { value: v, ref: `${key}=${v}` };
  }
  return null;
}

function isWalkable(tags: Record<string, string>): boolean {
  const hw = tags.highway;
  if (!hw || NEVER_WALKABLE.has(hw)) return false;
  if (tags.foot === "no") return false;
  if (NEEDS_SIDEWALK_PROOF.has(hw)) {
    const sw = sidewalkTag(tags);
    return sw !== null && sw.value !== "no" && sw.value !== "none";
  }
  return true;
}

// ---------------------------------------------------------------------------
// Pedestrian cost model
//
// Four components, weights summing to 1.0. Every component reports the OSM tag
// it came from, or marks itself inferred when the tag is absent. Nothing here
// invents a number: an untagged street gets a stated default, and the UI is
// expected to say so rather than present it as measured.
//
// Slope is deliberately absent. Seattle hills matter for walking, but this dump
// carries no elevation, and a fabricated grade would be worse than none.
// ---------------------------------------------------------------------------

const W_SIDEWALK = 0.34;
const W_TRAFFIC = 0.28;
const W_LIGHTING = 0.2;
const W_CROSSING = 0.18;

/** Applied when a tag is missing. Mid-scale, and always flagged as inferred. */
const DEFAULT_SIDEWALK = 0.35;
const DEFAULT_LIGHTING = 0.4;

const TRAFFIC_BY_CLASS: Record<string, number> = {
  residential: 0.15,
  living_street: 0.05,
  unclassified: 0.3,
  tertiary: 0.45,
  tertiary_link: 0.45,
  secondary: 0.65,
  secondary_link: 0.65,
  primary: 0.85,
  primary_link: 0.85,
  trunk: 1.0,
  trunk_link: 1.0,
};

function parseMaxspeedMph(tags: Record<string, string>): number | null {
  const raw = tags.maxspeed;
  if (!raw) return null;
  const m = raw.match(/(\d+)/);
  if (!m) return null;
  const n = Number(m[1]);
  return raw.includes("mph") ? n : n * 0.621371;
}

function scoreSidewalk(tags: Record<string, string>): RiskEvidence {
  const both = tags["sidewalk:both"];
  const plain = tags.sidewalk;
  const left = tags["sidewalk:left"];
  const right = tags["sidewalk:right"];

  const present = (v?: string) => v === "yes" || v === "both" || v === "separate";
  const absent = (v?: string) => v === "no" || v === "none";

  if (present(both) || present(plain)) {
    return {
      type: "sidewalk",
      score: 0,
      detail: "sidewalk on both sides",
      ref: both ? `sidewalk:both=${both}` : `sidewalk=${plain}`,
      inferred: false,
    };
  }
  if (absent(both) || absent(plain)) {
    return {
      type: "sidewalk",
      score: 1,
      detail: "no sidewalk mapped on this block",
      ref: both ? `sidewalk:both=${both}` : `sidewalk=${plain}`,
      inferred: false,
    };
  }
  if (plain === "left" || plain === "right" || present(left) || present(right)) {
    const ref = plain
      ? `sidewalk=${plain}`
      : present(left)
        ? `sidewalk:left=${left}`
        : `sidewalk:right=${right}`;
    return {
      type: "sidewalk",
      score: 0.4,
      detail: "sidewalk on one side only",
      ref,
      inferred: false,
    };
  }
  return {
    type: "sidewalk",
    score: DEFAULT_SIDEWALK,
    detail: "sidewalk not mapped - neutral default applied",
    ref: null,
    inferred: true,
  };
}

function scoreLighting(tags: Record<string, string>): RiskEvidence {
  const lit = tags.lit;
  if (lit === "yes" || lit === "24/7") {
    return { type: "lighting", score: 0, detail: "street lit", ref: `lit=${lit}`, inferred: false };
  }
  if (lit === "no") {
    return { type: "lighting", score: 1, detail: "street mapped as unlit", ref: "lit=no", inferred: false };
  }
  return {
    type: "lighting",
    score: DEFAULT_LIGHTING,
    detail: "lighting not mapped - neutral default applied",
    ref: null,
    inferred: true,
  };
}

function scoreTraffic(tags: Record<string, string>): RiskEvidence {
  const hw = tags.highway ?? "unclassified";
  const base = TRAFFIC_BY_CLASS[hw] ?? 0.3;
  const mph = parseMaxspeedMph(tags);
  const speedTerm = mph === null ? base : Math.min(1, mph / 40);
  const score = mph === null ? base : base * 0.6 + speedTerm * 0.4;
  const detail =
    mph === null
      ? `${hw.replace(/_/g, " ")}, speed not mapped`
      : `${hw.replace(/_/g, " ")}, posted ${Math.round(mph)} mph`;
  return {
    type: "traffic",
    score,
    detail,
    ref: mph === null ? `highway=${hw}` : `highway=${hw}, maxspeed=${tags.maxspeed}`,
    inferred: mph === null,
  };
}

function scoreCrossing(tags: Record<string, string>): RiskEvidence {
  const lanes = Number(tags.lanes);
  if (!Number.isFinite(lanes) || lanes <= 0) {
    return {
      type: "crossing",
      score: 0.3,
      detail: "lane count not mapped - neutral default applied",
      ref: null,
      inferred: true,
    };
  }
  return {
    type: "crossing",
    score: Math.min(1, (lanes - 1) / 5),
    detail: `${lanes} lane${lanes === 1 ? "" : "s"} to cross`,
    ref: `lanes=${tags.lanes}`,
    inferred: false,
  };
}

/** Returns risk in [0,1] plus the evidence each component was derived from. */
export function scoreEdge(tags: Record<string, string>): {
  risk: number;
  evidence: RiskEvidence[];
} {
  const sidewalk = scoreSidewalk(tags);
  const lighting = scoreLighting(tags);
  const traffic = scoreTraffic(tags);
  const crossing = scoreCrossing(tags);
  const risk =
    sidewalk.score * W_SIDEWALK +
    traffic.score * W_TRAFFIC +
    lighting.score * W_LIGHTING +
    crossing.score * W_CROSSING;
  return {
    risk: Math.min(1, risk),
    evidence: [sidewalk, lighting, traffic, crossing],
  };
}

// ---------------------------------------------------------------------------
// Build
// ---------------------------------------------------------------------------

export type BuildOptions = {
  osmPath: string;
  /** [west, south, east, north]. Ways are kept if their bounds intersect it. */
  bbox: [number, number, number, number];
};

export function buildGraph(opts: BuildOptions): WalkGraph {
  const raw = JSON.parse(readFileSync(opts.osmPath, "utf8")) as { elements: OsmWay[] };
  const [W, S, E, N] = opts.bbox;

  const ways = raw.elements.filter(
    (w) =>
      w.type === "way" &&
      w.tags &&
      isWalkable(w.tags) &&
      w.bounds.minlon < E &&
      w.bounds.maxlon > W &&
      w.bounds.minlat < N &&
      w.bounds.maxlat > S,
  );

  // A node shared by two or more kept ways is a real junction. Way endpoints are
  // junctions too, otherwise a dead-end block would have nowhere to attach.
  const useCount = new Map<number, number>();
  for (const w of ways) {
    for (const n of w.nodes) useCount.set(n, (useCount.get(n) ?? 0) + 1);
  }
  const isJunction = (nodeId: number, way: OsmWay, idx: number) =>
    (useCount.get(nodeId) ?? 0) > 1 || idx === 0 || idx === way.nodes.length - 1;

  const nodes = new Map<number, LngLat>();
  const edges = new Map<string, Edge>();
  const adj = new Map<number, Edge[]>();

  const link = (from: number, edge: Edge) => {
    const list = adj.get(from);
    if (list) list.push(edge);
    else adj.set(from, [edge]);
  };

  for (const way of ways) {
    const { risk, evidence } = scoreEdge(way.tags);
    const name = way.tags.name ?? "unnamed street";

    let anchor = 0; // index of the junction the current block started at
    for (let i = 1; i < way.nodes.length; i++) {
      if (!isJunction(way.nodes[i], way, i)) continue;

      const a = way.nodes[anchor];
      const b = way.nodes[i];
      if (a === b) {
        anchor = i;
        continue;
      }

      const geometry: LngLat[] = way.geometry
        .slice(anchor, i + 1)
        .map((p) => [p.lon, p.lat] as LngLat);
      const length_m = polylineLength(geometry);
      anchor = i;

      // Sub-metre stubs are digitisation noise, not blocks.
      if (length_m < 1) continue;

      nodes.set(a, geometry[0]);
      nodes.set(b, geometry[geometry.length - 1]);

      // Stable id: same OSM way, same start node => same id on every rebuild.
      const segment_id = `sw:${way.id}:${a}`;
      const edge: Edge = {
        segment_id,
        way_id: way.id,
        a,
        b,
        name,
        geometry,
        length_m,
        risk,
        evidence,
        tags: way.tags,
      };
      edges.set(segment_id, edge);
      link(a, edge);
      link(b, edge);
    }
  }

  return { nodes, adj, edges, bbox: opts.bbox };
}

// ---------------------------------------------------------------------------
// Cross-street naming: "Pike St, 1st Ave -> 2nd Ave"
// ---------------------------------------------------------------------------

/** Names of other streets meeting at a junction, excluding the edge's own name. */
export function crossStreets(g: WalkGraph, nodeId: number, exclude: string): string[] {
  const seen = new Set<string>();
  for (const e of g.adj.get(nodeId) ?? []) {
    if (e.name !== exclude && e.name !== "unnamed street") seen.add(e.name);
  }
  return [...seen];
}

export function describeEdge(g: WalkGraph, e: Edge): string {
  const from = crossStreets(g, e.a, e.name)[0];
  const to = crossStreets(g, e.b, e.name)[0];
  if (from && to) return `${e.name}, ${from} → ${to}`;
  if (from) return `${e.name}, at ${from}`;
  if (to) return `${e.name}, at ${to}`;
  return e.name;
}

// ---------------------------------------------------------------------------
// Disk cache
// ---------------------------------------------------------------------------

type SerialisedGraph = {
  version: number;
  bbox: [number, number, number, number];
  nodes: [number, LngLat][];
  edges: Edge[];
};

const CACHE_VERSION = 3;

export function saveGraph(g: WalkGraph, path: string): void {
  mkdirSync(dirname(path), { recursive: true });
  const payload: SerialisedGraph = {
    version: CACHE_VERSION,
    bbox: g.bbox,
    nodes: [...g.nodes.entries()],
    edges: [...g.edges.values()],
  };
  writeFileSync(path, JSON.stringify(payload));
}

export function loadGraph(path: string): WalkGraph | null {
  if (!existsSync(path)) return null;
  try {
    const payload = JSON.parse(readFileSync(path, "utf8")) as SerialisedGraph;
    if (payload.version !== CACHE_VERSION) return null;
    const nodes = new Map(payload.nodes);
    const edges = new Map<string, Edge>();
    const adj = new Map<number, Edge[]>();
    for (const e of payload.edges) {
      edges.set(e.segment_id, e);
      for (const n of [e.a, e.b]) {
        const list = adj.get(n);
        if (list) list.push(e);
        else adj.set(n, [e]);
      }
    }
    return { nodes, adj, edges, bbox: payload.bbox };
  } catch {
    return null;
  }
}

/** Build, or reuse the cache when it is current. */
export function getGraph(opts: BuildOptions & { cachePath: string }): WalkGraph {
  const cached = loadGraph(opts.cachePath);
  if (cached && cached.bbox.join(",") === opts.bbox.join(",")) return cached;
  const g = buildGraph(opts);
  saveGraph(g, opts.cachePath);
  return g;
}

// Built directly (`bun run server/graph.ts`) it reports what it made.
if (import.meta.main) {
  const root = join(import.meta.dir, "..", "..", "..");
  const t0 = performance.now();
  const g = buildGraph({
    osmPath: join(root, "experiments/surukamera/data/osm_ways.json"),
    bbox: [-122.375, 47.585, -122.29, 47.65],
  });
  const ms = Math.round(performance.now() - t0);
  const lengths = [...g.edges.values()].map((e) => e.length_m);
  const risks = [...g.edges.values()].map((e) => e.risk);
  const inferred = [...g.edges.values()].filter((e) =>
    e.evidence.some((v) => v.inferred),
  ).length;
  console.log(`built in ${ms} ms`);
  console.log(`junctions : ${g.nodes.size}`);
  console.log(`blocks    : ${g.edges.size}`);
  console.log(
    `block len : min ${Math.min(...lengths).toFixed(0)} m, ` +
      `median ${lengths.sort((a, b) => a - b)[lengths.length >> 1].toFixed(0)} m, ` +
      `max ${Math.max(...lengths).toFixed(0)} m`,
  );
  console.log(
    `risk      : min ${Math.min(...risks).toFixed(3)}, ` +
      `mean ${(risks.reduce((a, b) => a + b, 0) / risks.length).toFixed(3)}, ` +
      `max ${Math.max(...risks).toFixed(3)}`,
  );
  console.log(
    `blocks with at least one inferred component: ${inferred} / ${g.edges.size} ` +
      `(${((inferred / g.edges.size) * 100).toFixed(0)}%)`,
  );
}
