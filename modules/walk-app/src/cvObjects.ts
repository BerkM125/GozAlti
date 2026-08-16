/**
 * Local-CNN detections as simple 3D meshes (demo-ui checklist row 7), ported
 * from the experimental console (experiments/berkan_testing/app.js, read-only
 * reference). No model files: each car/person is composed at runtime from a
 * few fill-extrusion polygons, so the layer costs nothing to ship and renders
 * on any MapLibre map.
 *
 * Honesty rules, inherited verbatim: the footprint stays the CNN's estimate
 * (only the visual shape/scale is styled here), and a detection without a
 * world-position estimate (`est: null`) is never drawn - no invented spots.
 */
import type { Feature, Polygon } from "geojson";
import type { CvDetection, CvEst, CvResult } from "./types.ts";

export type CvObjectProps = {
  label: string;
  conf: number;
  camera_id: string;
  color: string;
  base: number;
  height: number;
};

export type CvFeature = Feature<Polygon, CvObjectProps>;

/** Metre offsets [along-bearing, cross-bearing] -> lon/lat ring. Flat-earth
 *  scale is fine at street range (tens of metres). */
function localRing(est: CvEst, offsets: [number, number][]): [number, number][] {
  const mLat = 110574.0;
  const mLon = 111320.0 * Math.cos((est.lat * Math.PI) / 180);
  const b = ((est.bearing_deg || 0) * Math.PI) / 180;
  const ux = Math.sin(b);
  const uy = Math.cos(b); // along bearing (E, N)
  const vx = uy;
  const vy = -ux; // perpendicular
  return offsets.map(([a, c]) => [
    est.lon + (a * ux + c * vx) / mLon,
    est.lat + (a * uy + c * vy) / mLat,
  ]);
}

function rectRing(est: CvEst, cx: number, cy: number, len: number, wid: number) {
  return localRing(est, [
    [cx + len / 2, cy + wid / 2],
    [cx + len / 2, cy - wid / 2],
    [cx - len / 2, cy - wid / 2],
    [cx - len / 2, cy + wid / 2],
    [cx + len / 2, cy + wid / 2],
  ]);
}

function octRing(est: CvEst, cx: number, cy: number, r: number) {
  const pts: [number, number][] = [];
  for (let i = 0; i <= 8; i++) {
    const a = (2 * Math.PI * i) / 8;
    pts.push([cx + r * Math.cos(a), cy + r * Math.sin(a)]);
  }
  return localRing(est, pts);
}

function meshPart(
  d: CvDetection,
  cameraId: string,
  ring: [number, number][],
  base: number,
  height: number,
  color: string,
): CvFeature {
  return {
    type: "Feature",
    properties: { label: d.label, conf: d.conf, camera_id: cameraId, color, base, height },
    geometry: { type: "Polygon", coordinates: [ring] },
  };
}

const CAR_LABELS = new Set(["car", "truck", "bus"]);

/** Composed extrusion "mesh" per detection, oriented to the CNN's bearing. */
function objectMesh(d: CvDetection, cameraId: string): CvFeature[] {
  const [len, wid, h] = d.footprint_m!;
  const est = d.est!;

  if (d.label === "person") {
    // Bright-red simple person: cylindrical body + head, drawn a bit larger
    // than the raw footprint (still far smaller than any car).
    const H = Math.max(h, 1.7) * 1.15;
    const bodyR = Math.max(0.5, (Math.min(len, wid) / 2) * 1.6);
    return [
      meshPart(d, cameraId, octRing(est, 0, 0, bodyR), 0, H * 0.72, "#FF4D4D"),
      meshPart(d, cameraId, octRing(est, 0, 0, bodyR * 0.55), H * 0.74, H, "#FF8A80"),
    ];
  }

  if (CAR_LABELS.has(d.label)) {
    // Bright-yellow simple car: 4 wheels + body + cabin, oriented to bearing.
    const L = len * 1.1;
    const W = wid * 1.1;
    const H = Math.max(h, 1.4);
    const wx = L * 0.32;
    const wy = W / 2 - 0.12;
    const parts: CvFeature[] = [];
    for (const [ax, cy] of [
      [wx, wy],
      [wx, -wy],
      [-wx, wy],
      [-wx, -wy],
    ] as [number, number][]) {
      parts.push(meshPart(d, cameraId, rectRing(est, ax, cy, 0.7, 0.3), 0, H * 0.3, "#15181C"));
    }
    parts.push(meshPart(d, cameraId, rectRing(est, 0, 0, L, W), H * 0.12, H * 0.58, "#FFD60A"));
    parts.push(
      meshPart(d, cameraId, rectRing(est, -L * 0.06, 0, L * 0.45, W * 0.8), H * 0.58, H * 1.02, "#F2C200"),
    );
    return parts;
  }

  // Everything else keeps a plain box in the old palette.
  const color = d.label === "bicycle" || d.label === "motorbike" ? "#F6AD55" : "#4FD1C5";
  return [meshPart(d, cameraId, rectRing(est, 0, 0, len, wid), 0, h, color)];
}

/**
 * All drawable features for one CV result. `[]` for a failed pass; detections
 * lacking `est` or `footprint_m` are skipped, never approximated.
 */
export function cvResultFeatures(res: CvResult): CvFeature[] {
  if (!res.ok) return [];
  const feats: CvFeature[] = [];
  for (const d of res.detections ?? []) {
    if (!d.est || !d.footprint_m) continue;
    feats.push(...objectMesh(d, res.camera_id));
  }
  return feats;
}

/** How many detections in a result actually carry a world position. */
export const placedCount = (res: CvResult): number =>
  res.ok ? (res.detections ?? []).filter((d) => d.est && d.footprint_m).length : 0;
