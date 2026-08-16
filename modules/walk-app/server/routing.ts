/**
 * Pedestrian routing: Dijkstra and A* over the block graph.
 *
 * Edge cost is metres of *effective* walking - real distance inflated by the
 * block's risk:
 *
 *     cost = length_m * (1 + riskWeight * risk)
 *
 * riskWeight = 0 gives the plain shortest walk. riskWeight > 0 buys distance to
 * avoid missing sidewalks, unlit blocks and heavy traffic. Because risk >= 0 the
 * multiplier is never below 1, so straight-line distance stays an admissible A*
 * heuristic and A* returns the same optimum Dijkstra does.
 */

import {
  describeEdge,
  haversine,
  type Edge,
  type LngLat,
  type WalkGraph,
} from "./graph.ts";

export const WALK_M_PER_MIN = 80; // ~1.33 m/s, matches map-frontend's config
export const SAFER_RISK_WEIGHT = 3.0;
export const DETOUR_CAP = 1.25;

export type Algorithm = "astar" | "dijkstra";

// ---------------------------------------------------------------------------
// Min-heap keyed on f-score
// ---------------------------------------------------------------------------

class MinHeap {
  private keys: number[] = [];
  private vals: number[] = [];

  get size(): number {
    return this.keys.length;
  }

  push(key: number, val: number): void {
    this.keys.push(key);
    this.vals.push(val);
    let i = this.keys.length - 1;
    while (i > 0) {
      const p = (i - 1) >> 1;
      if (this.keys[p] <= this.keys[i]) break;
      this.swap(p, i);
      i = p;
    }
  }

  pop(): { key: number; val: number } | undefined {
    if (this.keys.length === 0) return undefined;
    const key = this.keys[0];
    const val = this.vals[0];
    const lastK = this.keys.pop()!;
    const lastV = this.vals.pop()!;
    if (this.keys.length > 0) {
      this.keys[0] = lastK;
      this.vals[0] = lastV;
      let i = 0;
      for (;;) {
        const l = 2 * i + 1;
        const r = l + 1;
        let s = i;
        if (l < this.keys.length && this.keys[l] < this.keys[s]) s = l;
        if (r < this.keys.length && this.keys[r] < this.keys[s]) s = r;
        if (s === i) break;
        this.swap(s, i);
        i = s;
      }
    }
    return { key, val };
  }

  private swap(i: number, j: number): void {
    [this.keys[i], this.keys[j]] = [this.keys[j], this.keys[i]];
    [this.vals[i], this.vals[j]] = [this.vals[j], this.vals[i]];
  }
}

// ---------------------------------------------------------------------------
// Spatial index for snapping a tap to the nearest junction
//
// safe-walk scans every node per endpoint. That is fine at 1.7k segments and a
// wall beyond it, so this buckets junctions into a degree grid instead and
// widens the search ring until it finds something.
// ---------------------------------------------------------------------------

const CELL_DEG = 0.004; // ~445 m north-south

export class NodeIndex {
  private cells = new Map<string, number[]>();

  constructor(private graph: WalkGraph) {
    for (const [id, [lon, lat]] of graph.nodes) {
      const k = NodeIndex.key(lon, lat);
      const bucket = this.cells.get(k);
      if (bucket) bucket.push(id);
      else this.cells.set(k, [id]);
    }
  }

  private static key(lon: number, lat: number): string {
    return `${Math.floor(lon / CELL_DEG)}:${Math.floor(lat / CELL_DEG)}`;
  }

  nearest(point: LngLat): number | null {
    const [lon, lat] = point;
    const cx = Math.floor(lon / CELL_DEG);
    const cy = Math.floor(lat / CELL_DEG);
    for (let ring = 0; ring <= 12; ring++) {
      let best: number | null = null;
      let bestD = Infinity;
      for (let dx = -ring; dx <= ring; dx++) {
        for (let dy = -ring; dy <= ring; dy++) {
          // Only the shell of the ring; the interior was covered already.
          if (ring > 0 && Math.abs(dx) !== ring && Math.abs(dy) !== ring) continue;
          for (const id of this.cells.get(`${cx + dx}:${cy + dy}`) ?? []) {
            const d = haversine(point, this.graph.nodes.get(id)!);
            if (d < bestD) {
              bestD = d;
              best = id;
            }
          }
        }
      }
      // One extra ring past the first hit, since a nearer node can sit just
      // across a cell boundary.
      if (best !== null) {
        for (let dx = -(ring + 1); dx <= ring + 1; dx++) {
          for (let dy = -(ring + 1); dy <= ring + 1; dy++) {
            if (Math.abs(dx) !== ring + 1 && Math.abs(dy) !== ring + 1) continue;
            for (const id of this.cells.get(`${cx + dx}:${cy + dy}`) ?? []) {
              const d = haversine(point, this.graph.nodes.get(id)!);
              if (d < bestD) {
                bestD = d;
                best = id;
              }
            }
          }
        }
        return best;
      }
    }
    return null;
  }
}

// ---------------------------------------------------------------------------
// Search
// ---------------------------------------------------------------------------

const edgeCost = (e: Edge, riskWeight: number) => e.length_m * (1 + riskWeight * e.risk);

export type SearchResult = {
  path: Edge[];
  cost: number;
  /** Junctions removed from the frontier. Reported so A* can be shown to win. */
  expanded: number;
};

/** Shared relaxation loop. `heuristic` returning 0 for every node is Dijkstra. */
function search(
  g: WalkGraph,
  source: number,
  target: number,
  riskWeight: number,
  heuristic: (node: number) => number,
): SearchResult | null {
  const dist = new Map<number, number>([[source, 0]]);
  const cameFrom = new Map<number, { node: number; edge: Edge }>();
  const settled = new Set<number>();
  const open = new MinHeap();
  open.push(heuristic(source), source);
  let expanded = 0;

  while (open.size > 0) {
    const { val: node } = open.pop()!;
    if (settled.has(node)) continue;
    settled.add(node);
    expanded++;

    if (node === target) {
      const path: Edge[] = [];
      let cur = node;
      while (cur !== source) {
        const step = cameFrom.get(cur);
        if (!step) return null;
        path.push(step.edge);
        cur = step.node;
      }
      path.reverse();
      return { path, cost: dist.get(target)!, expanded };
    }

    const d = dist.get(node)!;
    for (const edge of g.adj.get(node) ?? []) {
      const next = edge.a === node ? edge.b : edge.a;
      if (settled.has(next)) continue;
      const nd = d + edgeCost(edge, riskWeight);
      if (nd < (dist.get(next) ?? Infinity)) {
        dist.set(next, nd);
        cameFrom.set(next, { node, edge });
        open.push(nd + heuristic(next), next);
      }
    }
  }
  return null;
}

export function dijkstra(
  g: WalkGraph,
  source: number,
  target: number,
  riskWeight: number,
): SearchResult | null {
  return search(g, source, target, riskWeight, () => 0);
}

export function astar(
  g: WalkGraph,
  source: number,
  target: number,
  riskWeight: number,
): SearchResult | null {
  const goal = g.nodes.get(target);
  if (!goal) return null;
  // Straight-line metres. Admissible: every edge costs at least its length.
  const h = (node: number) => {
    const p = g.nodes.get(node);
    return p ? haversine(p, goal) : 0;
  };
  return search(g, source, target, riskWeight, h);
}

// ---------------------------------------------------------------------------
// Contract shapes (SPEC.md §6.4, §6.6)
// ---------------------------------------------------------------------------

export type EvidenceItem = { type: string; ref: string; detail: string; score: number };

export type SegmentAssessment = {
  segment_id: string;
  risk: number;
  evidence: EvidenceItem[];
  updated_at: string;
  // Extensions the map needs to draw and label the block.
  name: string;
  geometry: { type: "LineString"; coordinates: LngLat[] };
  length_m: number;
  inferred: boolean;
};

export type Route = {
  kind: "shortest" | "safer";
  /** SPEC §6.6 is [lat, lon]. GeoJSON order is flipped at the map boundary. */
  polyline: [number, number][];
  length_m: number;
  segment_ids: string[];
  cameras_en_route: string[];
  evidence_summary: string;
};

export type RouteResult = {
  shortest: Route;
  safer: Route;
  segments: SegmentAssessment[];
  detour_ratio: number;
  over_cap: boolean;
  cap: number;
  /** How the two searches actually performed, for the demo's honesty. */
  stats: {
    algorithm: Algorithm;
    expanded_shortest: number;
    expanded_safer: number;
    ms: number;
  };
};

/** Walks the edge list in travel order and emits an ordered [lat, lon] line. */
function stitchPolyline(path: Edge[], source: number): [number, number][] {
  const out: [number, number][] = [];
  let at = source;
  for (const e of path) {
    const forward = e.a === at;
    const coords = forward ? e.geometry : [...e.geometry].reverse();
    for (const [lon, lat] of coords) {
      const last = out[out.length - 1];
      if (!last || last[0] !== lat || last[1] !== lon) out.push([lat, lon]);
    }
    at = forward ? e.b : e.a;
  }
  return out;
}

const totalLength = (path: Edge[]) => path.reduce((m, e) => m + e.length_m, 0);

export function assess(g: WalkGraph, path: Edge[]): SegmentAssessment[] {
  const now = new Date().toISOString();
  return path.map((e) => ({
    segment_id: e.segment_id,
    risk: Number(e.risk.toFixed(3)),
    evidence: e.evidence.map((v) => ({
      type: v.type,
      // "osm:untagged" is the whole inferred signal on the wire; the UI turns
      // it into a "not mapped" chip rather than a sentence baked into detail.
      ref: v.ref ?? "osm:untagged",
      detail: v.detail,
      score: Number(v.score.toFixed(2)),
    })),
    updated_at: now,
    name: describeEdge(g, e),
    geometry: { type: "LineString" as const, coordinates: e.geometry },
    length_m: Math.round(e.length_m),
    inferred: e.evidence.some((v) => v.inferred),
  }));
}

/**
 * Says what the detour bought, in checkable terms only. It counts blocks the
 * safer route avoided and names the worst thing about them - it never asserts
 * that anywhere is dangerous.
 */
function summarise(shortest: Edge[], safer: Edge[]): string {
  const saferIds = new Set(safer.map((e) => e.segment_id));
  const avoided = shortest.filter((e) => !saferIds.has(e.segment_id));
  const extra = Math.round(totalLength(safer) - totalLength(shortest));

  if (avoided.length === 0) {
    return "the shortest walk is already the lowest-risk one; no detour needed";
  }

  const noSidewalk = avoided.filter((e) =>
    e.evidence.some((v) => v.type === "sidewalk" && v.score >= 1 && !v.inferred),
  ).length;
  const unlit = avoided.filter((e) =>
    e.evidence.some((v) => v.type === "lighting" && v.score >= 1 && !v.inferred),
  ).length;

  const reasons: string[] = [];
  if (noSidewalk > 0) reasons.push(`${noSidewalk} with no mapped sidewalk`);
  if (unlit > 0) reasons.push(`${unlit} mapped unlit`);

  const tail = reasons.length > 0 ? ` (${reasons.join(", ")})` : "";
  const cost = extra > 0 ? `, ${extra} m further` : "";
  return `avoids ${avoided.length} block${avoided.length === 1 ? "" : "s"}${tail}${cost}`;
}

export function planRoute(
  g: WalkGraph,
  index: NodeIndex,
  from: LngLat,
  to: LngLat,
  algorithm: Algorithm = "astar",
): RouteResult | { error: string } {
  const source = index.nearest(from);
  const target = index.nearest(to);
  if (source === null || target === null) {
    return { error: "no walkable street near one of those points" };
  }
  if (source === target) {
    return { error: "start and destination snap to the same junction" };
  }

  const run = algorithm === "dijkstra" ? dijkstra : astar;
  const t0 = performance.now();
  const shortest = run(g, source, target, 0);
  const safer = run(g, source, target, SAFER_RISK_WEIGHT);
  const ms = Math.round(performance.now() - t0);

  if (!shortest || !safer) return { error: "no walking route connects those points" };

  const shortestLen = totalLength(shortest.path);
  const saferLen = totalLength(safer.path);
  const detour_ratio = shortestLen > 0 ? saferLen / shortestLen : 1;

  return {
    shortest: {
      kind: "shortest",
      polyline: stitchPolyline(shortest.path, source),
      length_m: Math.round(shortestLen),
      segment_ids: shortest.path.map((e) => e.segment_id),
      cameras_en_route: [],
      evidence_summary: "shortest walk, no risk weighting applied",
    },
    safer: {
      kind: "safer",
      polyline: stitchPolyline(safer.path, source),
      length_m: Math.round(saferLen),
      segment_ids: safer.path.map((e) => e.segment_id),
      cameras_en_route: [],
      evidence_summary: summarise(shortest.path, safer.path),
    },
    segments: assess(g, safer.path),
    detour_ratio: Number(detour_ratio.toFixed(3)),
    over_cap: detour_ratio > DETOUR_CAP,
    cap: DETOUR_CAP,
    stats: {
      algorithm,
      expanded_shortest: shortest.expanded,
      expanded_safer: safer.expanded,
      ms,
    },
  };
}
