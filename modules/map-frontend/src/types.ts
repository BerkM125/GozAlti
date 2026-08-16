export type LngLat = [number, number];
export type LatLng = [number, number];

export type RiskParts = {
  lighting: number;
  sidewalk: number;
  crossings: number;
  traffic: number;
  collisions: number;
};

// Per-segment risk detail. Not part of SPEC.md's Route (§6.6) — harness attaches
// it under `segments` as a bridge for the evidence sheet until synthesis's
// SegmentAssessment (§6.4) is wired in; swap to that contract when it lands.
export type SegmentDetail = {
  segment_id: string;
  name: string;
  geometry: { type: "LineString"; coordinates: LngLat[] };
  base_risk: number;
  risk_parts: RiskParts;
  live_penalty: number;
  confidence: number;
  stale: boolean;
  camera?: { id: string; thumb_url: string; ts: string; surface: string; occlusion: string };
};

// SPEC.md §6.6 — harness -> map-frontend
export type Route = {
  kind: "shortest" | "safer";
  polyline: LatLng[];
  length_m: number;
  segment_ids: string[];
  cameras_en_route: string[];
  evidence_summary: string;
  segments?: SegmentDetail[]; // extension, see SegmentDetail above
};

export type RoutePair = { shortest: Route; safer: Route };

export type Refusal = {
  error: "detour_cap_exceeded";
  detour_ratio: number;
  cap: number;
  message: string;
};

export const RISK_LABELS: Record<keyof RiskParts, string> = {
  lighting: "Lighting",
  sidewalk: "Sidewalk",
  crossings: "Crossings",
  traffic: "Traffic",
  collisions: "Collisions",
};
