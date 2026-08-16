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

/** `score` is the component's 0-1 contribution; optional because §6.5 Alerts
 *  reuse this shape and synthesis does not send scores. */
export type EvidenceItem = { type: string; ref: string; detail: string; score?: number };

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

/**
 * SPEC.md §6.7 - one camera in a convergence result.
 *
 * `live_hls` and `snapshot_url` arrive as this app's own same-origin proxy
 * paths, not the upstream SDOT/Wowza URLs §6.7's example shows. The walk-app
 * server rewrites them (see `server/index.ts`) so no byte reaches SDOT without
 * passing media-ingest's rate limiter.
 */
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
  /**
   * How far off your way this camera sits: distance to the point you asked
   * about, or perpendicular distance to the route when a route is active.
   */
  distance_m?: number | null;
  /** How far along the route the camera sits. Null when there is no route. */
  along_m?: number | null;
  /** Intersection this camera watches, e.g. "2nd Ave & Spring St". */
  desc?: string | null;
  street?: string | null;
};

export type Convergence = { cameras: Camera[] } | Unavailable;

/**
 * One VLM read of one frame, as media-ingest's `/api/detections/{cid}` serves
 * it. This is media-ingest's live-state record, which wraps a §6.1 FrameRecord
 * and carries §6.2's detections - it is not literally the §6.2 Observation.
 */
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
  frame?: FrameRecord;
  model: string;
  detections: Detection[];
  caption: string;
};

/**
 * SPEC.md §6.1 - what media-ingest last actually pulled for this camera.
 *
 * This, not the VLM read, is what makes the age badge possible: a camera with
 * no VLM backend still has a frame with a timestamp, and invariant #2 says
 * every image carries an honest age. `source` distinguishes a frame decoded
 * from the live stream (~6 s old) from a polled JPEG (up to 60 s) from one
 * served off disk because the rate gate was shut.
 */
export type FrameRecord = {
  camera_id: string;
  captured_at: string;
  lat?: number | null;
  lon?: number | null;
  kind?: string;
  path?: string | null;
  source?: "sdot-snapshot" | "sdot-hls" | "disk-cache" | "phone" | string;
  stale?: boolean;
};

/**
 * One result from `/api/geocode` - a street or intersection the walk graph can
 * route to, resolved offline. No external geocoder is involved.
 */
export type Place = {
  label: string;
  kind: "street" | "intersection";
  lat: number;
  lon: number;
};

/** An endpoint of the planned walk: where, plus the name the UI shows for it. */
export type TripStop = {
  point: LngLat;
  label: string;
};

// ---------------------------------------------------------------------------
// modules/pathfinding PathObject — the consolidated router (god SPEC §5.1
// spike 4), served by media-ingest :8030 /api/route and proxied here as
// /api/path. Shapes mirror modules/pathfinding/SPEC.md exactly.
// ---------------------------------------------------------------------------

export type RiskBucket = "low" | "medium" | "high";

export type PathSegment = {
  segment_id: string;
  name: string;
  geometry: { type: "LineString"; coordinates: LngLat[] };
  length_m: number;
  /** Routing weight in [0,1]. Stays out of the UI as a bare number. */
  risk: number;
  live_risk: number;
  base_risk: number;
  risk_bucket: RiskBucket;
  /** Every cost-model part, named and already weighted. The evidence. */
  risk_parts: Record<string, number>;
  /** Camera ids whose coverage includes this block. */
  cameras: string[];
};

export type PathCameraDetail = {
  camera_id: string;
  lat: number;
  lon: number;
  location_desc?: string | null;
  has_stream?: boolean;
  active?: boolean | null;
  last_person_at?: string | null;
};

export type PathRefuge = {
  osm_id?: number | string;
  name?: string | null;
  lat: number;
  lon: number;
  dist_m: number;
  open_until?: string | null;
  kind?: string | null;
  open_now?: boolean;
};

export type PathLiveMeta = {
  incorporated: boolean;
  basis: string;
  layers_pending: string[];
  layers_incorporated?: string[];
  cameras_reporting?: number;
};

export type PathObject = {
  path_id: string;
  version: number;
  kind: "shortest" | "safer";
  live: PathLiveMeta;
  night: boolean;
  daylight: boolean;
  detour_cap_hit: boolean;
  polyline: LatLng[];
  length_m: number;
  eta_min: number;
  segments: PathSegment[];
  cameras_en_route: string[];
  cameras_en_route_detail: PathCameraDetail[];
  cv_detections: Record<string, unknown>;
  refuges_en_route: PathRefuge[];
  evidence_summary: string;
  risk_basis: string;
  compute_ms: number;
};

/** Both kinds of one trip, fetched together so the dock can compare them. */
export type PathPair = { safer: PathObject; shortest: PathObject };

/**
 * Human name + stated source for each cost-model part, verbatim from
 * modules/pathfinding/SPEC.md. Parts marked live only move once the
 * PathLiveSession has incorporated camera evidence.
 */
export const RISK_PART_LABEL: Record<string, { name: string; source: string; live?: boolean }> = {
  traffic: { name: "Traffic", source: "OSM road class" },
  lighting: { name: "Lighting", source: "OSM lit tag / class prior, scaled down in daylight" },
  sidewalk: { name: "Sidewalk", source: "OSM sidewalk tags" },
  collisions: { name: "Collisions", source: "SDOT recorded ped/cyclist collisions per 100 m" },
  coverage: { name: "Camera coverage", source: "distance to the nearest public camera" },
  osint: { name: "Reports", source: "area sentiment signals (osint)" },
  crossings: { name: "Crossings", source: "junction density" },
  occupancy: { name: "People on cameras", source: "live camera occupancy, night rule", live: true },
  vlm_flags: { name: "Camera flags", source: "live VLM reads (e.g. blocked sidewalk)", live: true },
};

/** Every upstream call can come back like this, and the UI must render it. */
export type Unavailable = { ok: false; why: string };

export const isUnavailable = (v: unknown): v is Unavailable =>
  typeof v === "object" && v !== null && (v as Unavailable).ok === false;

/**
 * True only for a camera read that actually happened.
 *
 * `/api/detections/{cid}` answers `{}` for a camera nothing has analysed yet,
 * which `isUnavailable` does not catch because there is no `ok: false`. Storing
 * that as an observation makes the UI say the camera returned no description
 * when in truth it was never read - so every consumer goes through here.
 */
export const isObservation = (v: unknown): v is Observation =>
  typeof v === "object" && v !== null && (v as Observation).ok === true;

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
