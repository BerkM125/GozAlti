/** Shapes shared with the server. The contract ones mirror SPEC.md §6 exactly. */

export type LngLat = [number, number]; // GeoJSON order, what MapLibre wants
export type LatLng = [number, number]; // SPEC.md §6 order

/** SPEC.md §6.6 - harness -> map-frontend */
export type Route = {
  kind: "shortest" | "safer";
  polyline: LatLng[];
  length_m: number;
  segment_ids: string[];
  cameras_en_route: string[];
  evidence_summary: string;
};

export type EvidenceItem = { type: string; ref: string; detail: string };

/** SPEC.md §6.4, plus the geometry and label the map needs to draw it. */
export type SegmentAssessment = {
  segment_id: string;
  risk: number;
  evidence: EvidenceItem[];
  updated_at: string;
  name: string;
  geometry: { type: "LineString"; coordinates: LngLat[] };
  length_m: number;
  /** True when any component fell back to a default because OSM had no tag. */
  inferred: boolean;
};

export type RouteResult = {
  shortest: Route;
  safer: Route;
  segments: SegmentAssessment[];
  detour_ratio: number;
  over_cap: boolean;
  cap: number;
  stats: {
    algorithm: "astar" | "dijkstra";
    expanded_shortest: number;
    expanded_safer: number;
    ms: number;
  };
};

/** SPEC.md §6.7 - one camera in a convergence result. */
export type Camera = {
  camera_id: string;
  lat: number;
  lon: number;
  bearing_deg: number | null;
  bearing_conf: number | null;
  live_hls: string | null;
  snapshot_url: string | null;
  /** media-ingest extension: pixel change between two timestamped frames. */
  active?: boolean | null;
  last_activity_at?: string | null;
  distance_m?: number;
};

export type Convergence = { cameras: Camera[] } | Unavailable;

/** SPEC.md §6.2 - one VLM read of one frame. */
export type Detection = {
  label: string;
  cx: number;
  cy: number;
  conf: number;
  /** Absent when the camera's bearing is unresolved. Never guessed. */
  est?: {
    lat: number;
    lon: number;
    range_m: number;
    bearing_deg: number;
    method: string;
  } | null;
};

export type Observation = {
  camera_id: string;
  analyzed_at: string;
  ok: true;
  frame?: { captured_at: string; stale?: boolean };
  model: string;
  detections: Detection[];
  caption: string;
};

/** Every upstream call can come back like this, and the UI must render it. */
export type Unavailable = { ok: false; why: string };

export const isUnavailable = (v: unknown): v is Unavailable =>
  typeof v === "object" && v !== null && (v as Unavailable).ok === false;

/** SPEC.md §6.5 - live push. */
export type Alert = {
  id: string;
  severity: "info" | "caution" | "danger";
  lat: number;
  lon: number;
  segment_id: string;
  message: string;
  evidence: EvidenceItem[];
  issued_at: string;
};

// ---------------------------------------------------------------------------
// Detection classes
//
// Eleven labels, four families. Within a family the shape and glyph carry the
// difference, so the map never needs eleven competing hues.
// ---------------------------------------------------------------------------

export type DetectionFamily = "person" | "vehicle" | "obstruction" | "other";

const FAMILY: Record<string, DetectionFamily> = {
  person: "person",
  crowd: "person",
  car: "vehicle",
  truck: "vehicle",
  bus: "vehicle",
  bicycle: "vehicle",
  motorcycle: "vehicle",
  construction: "obstruction",
  debris: "obstruction",
  dog: "other",
  other: "other",
};

export const familyOf = (label: string): DetectionFamily => FAMILY[label] ?? "other";

export const FAMILY_LABEL: Record<DetectionFamily, string> = {
  person: "People",
  vehicle: "Vehicles",
  obstruction: "Obstructions",
  other: "Other",
};

/** Evidence types get a short human name for the sheet. */
export const EVIDENCE_LABEL: Record<string, string> = {
  sidewalk: "Sidewalk",
  lighting: "Lighting",
  traffic: "Traffic",
  crossing: "Crossing",
  collision: "Collisions",
  vlm: "Camera read",
  osint: "Reports",
};
