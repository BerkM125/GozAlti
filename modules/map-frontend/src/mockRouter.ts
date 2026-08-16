// Synthetic router for mock mode. Draws a direct line and a bowed "safer"
// detour between the two tapped points, so the demo responds to the map
// instead of replaying one fixed route. Geometry is invented — it does not
// follow streets. Swap USE_MOCK off in config.ts once synthesis lands;
// nothing else imports this file.
import fixture from "./fixtures/route.json";
import type { LatLng, LngLat, Route, RoutePair, SegmentDetail } from "./types";

// Risk profiles are borrowed from the fixture so the segment sheet still shows
// realistic bars, names and camera stubs.
const TEMPLATE = (fixture as unknown as RoutePair).safer.segments ?? [];

const M_PER_DEG_LAT = 110574;
const mPerDegLng = (lat: number) => 111320 * Math.cos((lat * Math.PI) / 180);

const STEPS = 21; // 22 points along the safer curve
const SEGS = 7; // 3 steps per segment, so the split lands on sample points
const BOW = 0.22; // peak offset as a fraction of the chord ≈ 1.15× detour

type XY = [number, number];

// Same two taps always produce the same route.
function hash(ns: number[]): number {
  let h = 2166136261;
  for (const n of ns) {
    const v = Math.round(n * 1e5);
    h = Math.imul(h ^ (v & 0xffff), 16777619);
    h = Math.imul(h ^ (v >>> 16), 16777619);
  }
  return (h >>> 0) / 2 ** 32;
}

const length = (ps: XY[]) =>
  ps.slice(1).reduce((a, p, i) => a + Math.hypot(p[0] - ps[i][0], p[1] - ps[i][1]), 0);

function evidenceSummary(
  kind: "shortest" | "safer",
  detourRatio: number,
  segments: SegmentDetail[],
): string {
  if (kind !== "safer" || segments.length === 0) return "shortest path, no risk weighting";
  const avoided = segments.filter((s) => s.base_risk >= 0.35).length;
  return `weighted away from ${avoided} of ${segments.length} higher-risk segment(s); ${detourRatio.toFixed(2)}x the direct distance`;
}

export function mockRoutes(origin: LngLat, dest: LngLat): RoutePair {
  const [lng0, lat0] = origin;
  const kx = mPerDegLng(lat0);
  const fromXY = ([x, y]: XY): LngLat => [lng0 + x / kx, lat0 + y / M_PER_DEG_LAT];
  const toLatLng = (p: LngLat): LatLng => [p[1], p[0]];

  const X = (dest[0] - lng0) * kx;
  const Y = (dest[1] - lat0) * M_PER_DEG_LAT;
  const chord = Math.hypot(X, Y);

  const buildRoute = (
    kind: "shortest" | "safer",
    pts: XY[],
    detourRatio: number,
    segments: SegmentDetail[],
  ): Route => {
    const m = length(pts);
    return {
      kind,
      polyline: pts.map(fromXY).map(toLatLng),
      length_m: Math.round(m),
      segment_ids: segments.map((s) => s.segment_id),
      cameras_en_route: [],
      evidence_summary: evidenceSummary(kind, detourRatio, segments),
      segments,
    };
  };

  // Two taps in effectively the same spot: nothing to route.
  if (chord < 5) {
    const flat: XY[] = [
      [0, 0],
      [X, Y],
    ];
    return { shortest: buildRoute("shortest", flat, 1, []), safer: buildRoute("safer", flat, 1, []) };
  }

  // Bow the safer route off to one side with a quadratic bezier, then rejoin.
  const r = hash([...origin, ...dest]);
  const side = r < 0.5 ? -1 : 1;
  const h = chord * BOW * (0.8 + 0.4 * r);
  const cx = X / 2 + side * 2 * h * (-Y / chord);
  const cy = Y / 2 + side * 2 * h * (X / chord);

  const curve: XY[] = [];
  for (let i = 0; i <= STEPS; i++) {
    const t = i / STEPS;
    const u = 1 - t;
    curve.push([2 * u * t * cx + t * t * X, 2 * u * t * cy + t * t * Y]);
  }

  const straight: XY[] = [0, 1, 2, 3].map((i) => [(X * i) / 3, (Y * i) / 3]);

  const per = STEPS / SEGS;
  const segments: SegmentDetail[] = Array.from({ length: SEGS }, (_, i): SegmentDetail => {
    const t = TEMPLATE[i % TEMPLATE.length];
    return {
      ...t,
      segment_id: `sw:mock-${1000 + i}`,
      geometry: {
        type: "LineString",
        coordinates: curve.slice(i * per, i * per + per + 1).map(fromXY),
      },
    };
  });

  const detourRatio = Math.round((length(curve) / length(straight)) * 100) / 100;
  return {
    shortest: buildRoute("shortest", straight, 1, []),
    safer: buildRoute("safer", curve, detourRatio, segments),
  };
}
