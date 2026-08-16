/**
 * Routing smoke test: `bun run server/smoke.ts`
 *
 * Routes Pike Place Market -> Pioneer Square and asserts the things that would
 * quietly ruin the demo: that A* and Dijkstra agree on the optimum, that A*
 * expands fewer junctions, and that the safer route is never shorter than the
 * shortest one.
 */

import { join } from "node:path";
import { buildGraph } from "./graph.ts";
import { NodeIndex, planRoute, dijkstra, astar } from "./routing.ts";

const ROOT = join(import.meta.dir, "..", "..", "..");
const BBOX: [number, number, number, number] = [-122.375, 47.585, -122.29, 47.65];

const g = buildGraph({
  osmPath: join(ROOT, "experiments/surukamera/data/osm_ways.json"),
  bbox: BBOX,
});
const idx = new NodeIndex(g);

const from: [number, number] = [-122.3414, 47.6097]; // Pike Place Market
const to: [number, number] = [-122.3343, 47.6015]; // Pioneer Square

let failures = 0;
const check = (label: string, ok: boolean, note = "") => {
  console.log(`${ok ? "  ok  " : " FAIL "} ${label}${note ? `  ${note}` : ""}`);
  if (!ok) failures++;
};

console.log(`graph: ${g.nodes.size} junctions, ${g.edges.size} blocks\n`);

for (const algorithm of ["dijkstra", "astar"] as const) {
  const r = planRoute(g, idx, from, to, algorithm);
  if ("error" in r) {
    console.log(`${algorithm}: ERROR ${r.error}`);
    failures++;
    continue;
  }
  console.log(`--- ${algorithm} ---`);
  console.log(
    `shortest ${r.shortest.length_m} m over ${r.shortest.segment_ids.length} blocks`,
  );
  console.log(`safer    ${r.safer.length_m} m over ${r.safer.segment_ids.length} blocks`);
  console.log(`detour   ${r.detour_ratio}x (cap ${r.cap}, over=${r.over_cap})`);
  console.log(
    `expanded shortest=${r.stats.expanded_shortest} safer=${r.stats.expanded_safer} in ${r.stats.ms} ms`,
  );
  console.log(`summary  ${r.safer.evidence_summary}\n`);

  check(`${algorithm}: safer is not shorter than shortest`, r.safer.length_m >= r.shortest.length_m);
  check(`${algorithm}: polyline is continuous`, r.safer.polyline.length > r.safer.segment_ids.length);
  check(`${algorithm}: every segment carries evidence`, r.segments.every((s) => s.evidence.length === 4));
  check(
    `${algorithm}: every evidence row has a ref`,
    r.segments.every((s) => s.evidence.every((e) => e.ref.length > 0)),
  );

  if (algorithm === "astar") {
    console.log("\nsample of the safer route's evidence:");
    for (const s of r.segments.slice(0, 3)) {
      console.log(`  ${s.name}  [risk ${s.risk}, ${s.length_m} m]`);
      for (const ev of s.evidence) {
        console.log(`      ${ev.type.padEnd(9)} ${ev.detail}  <${ev.ref}>`);
      }
    }
    console.log();
  }
}

// A* must find the same optimum as Dijkstra, and touch less of the graph.
const s = idx.nearest(from)!;
const t = idx.nearest(to)!;
for (const w of [0, 3]) {
  const d = dijkstra(g, s, t, w)!;
  const a = astar(g, s, t, w)!;
  check(
    `riskWeight=${w}: A* cost equals Dijkstra`,
    Math.abs(d.cost - a.cost) < 1e-6,
    `(${d.cost.toFixed(2)} vs ${a.cost.toFixed(2)})`,
  );
  check(
    `riskWeight=${w}: A* expands fewer junctions`,
    a.expanded < d.expanded,
    `(${a.expanded} vs ${d.expanded})`,
  );
}

console.log(failures === 0 ? "\nall checks passed" : `\n${failures} check(s) failed`);
process.exit(failures === 0 ? 0 : 1);
