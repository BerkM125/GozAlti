import { API_BASE } from "./config.ts";
import { isUnavailable } from "./types.ts";
import type {
  Convergence,
  FrameRecord,
  LatLng,
  LngLat,
  Observation,
  PathObject,
  PathPair,
  Place,
  RouteResult,
  SegmentAssessment,
  Unavailable,
} from "./types.ts";
import type { FeatureCollection } from "geojson";

async function get<T>(path: string, timeoutMs = 10_000): Promise<T | Unavailable> {
  try {
    const res = await fetch(`${API_BASE}${path}`, { signal: AbortSignal.timeout(timeoutMs) });
    if (!res.ok) return { ok: false, why: `request failed (${res.status})` };
    return (await res.json()) as T;
  } catch {
    return { ok: false, why: "couldn't reach the walk-app server" };
  }
}

export class RouteError extends Error {}

// ---------------------------------------------------------------------------
// Consolidated router (modules/pathfinding via /api/path)
// ---------------------------------------------------------------------------

/** The router's error codes, phrased for a person holding a phone. */
const PATH_ERRORS: Record<string, string> = {
  out_of_area: "That point is outside the covered area (downtown through the U-District).",
  too_close: "Those two points are too close together to route between.",
  no_route: "No walkable route connects those points.",
};

async function fetchOnePath(
  origin: LngLat,
  dest: LngLat,
  kind: "safer" | "shortest",
): Promise<PathObject | Unavailable> {
  let res: Response;
  try {
    res = await fetch(
      `${API_BASE}/api/path?from=${origin[1]},${origin[0]}&to=${dest[1]},${dest[0]}&kind=${kind}&live=false`,
      { signal: AbortSignal.timeout(20_000) },
    );
  } catch {
    return { ok: false, why: "couldn't reach the walk-app server" };
  }
  if (res.status === 422) {
    // Genuinely unroutable — the local fallback router covers a smaller area,
    // so falling back would not help. Tell the user instead.
    const body = (await res.json().catch(() => null)) as { detail?: { error?: string } } | null;
    const code = body?.detail?.error ?? "no_route";
    throw new RouteError(PATH_ERRORS[code] ?? `routing failed (${code})`);
  }
  if (res.status === 503) return (await res.json()) as Unavailable;
  if (!res.ok) return { ok: false, why: `consolidated router returned ${res.status}` };
  return (await res.json()) as PathObject;
}

/**
 * Both kinds of one trip from the consolidated router, instant one-and-done
 * (no live session yet — that wiring is the next port). Returns Unavailable
 * when media-ingest is down so the caller can fall back to the local router;
 * throws RouteError when the router answered but the trip is unroutable.
 */
export async function fetchPath(origin: LngLat, dest: LngLat): Promise<PathPair | Unavailable> {
  const [safer, shortest] = await Promise.all([
    fetchOnePath(origin, dest, "safer"),
    fetchOnePath(origin, dest, "shortest"),
  ]);
  if (isUnavailable(safer)) return safer;
  if (isUnavailable(shortest)) return shortest;
  return { safer, shortest };
}

export async function fetchRoute(
  origin: LngLat,
  dest: LngLat,
  algorithm: "astar" | "dijkstra" = "astar",
): Promise<RouteResult> {
  const res = await fetch(`${API_BASE}/api/route`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ origin, dest, algorithm }),
    signal: AbortSignal.timeout(15_000),
  });
  if (res.status === 422) {
    const body = (await res.json()) as { error: string };
    throw new RouteError(body.error);
  }
  if (!res.ok) throw new RouteError(`routing failed (${res.status})`);
  return (await res.json()) as RouteResult;
}

/**
 * Streets and intersections matching `q`, answered by the walk-app server from
 * its own graph. An empty list is a real answer ("nothing routable matches"),
 * so an unreachable server degrades to that rather than throwing mid-keystroke.
 */
export async function searchPlaces(q: string): Promise<Place[]> {
  const res = await get<{ results: Place[] }>(`/api/geocode?q=${encodeURIComponent(q)}`, 6000);
  if (isUnavailable(res) || !Array.isArray(res.results)) return [];
  return res.results;
}

export const fetchCameras = (lat: number, lon: number, radiusM: number) =>
  get<Convergence>(`/api/cameras?lat=${lat}&lon=${lon}&radius_m=${radiusM}`);

/**
 * Every camera watching the route, in the order you pass them.
 *
 * A route is a line, so this cannot be a radius query around one point - that
 * is what limited the app to the cameras near wherever you happened to be.
 */
export async function fetchRouteCameras(
  polyline: LatLng[],
  radiusM?: number,
): Promise<Convergence> {
  try {
    const res = await fetch(`${API_BASE}/api/cameras/route`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ polyline, radius_m: radiusM }),
      signal: AbortSignal.timeout(10_000),
    });
    if (!res.ok) return { ok: false, why: `request failed (${res.status})` };
    return (await res.json()) as Convergence;
  } catch {
    return { ok: false, why: "couldn't reach the walk-app server" };
  }
}

/** Every walkable block with its routing weight; static per server boot. */
export const fetchBlocks = () => get<FeatureCollection>("/api/blocks", 15_000);

/** One block's §6.4 assessment, for the tap-anywhere evidence sheet. */
export const fetchSegment = (id: string) =>
  get<SegmentAssessment>(`/api/segment/${encodeURIComponent(id)}`, 6000);

export const fetchDetections = (cameraId: string) =>
  get<Observation>(`/api/detections/${encodeURIComponent(cameraId)}`, 20_000);

/**
 * The §6.1 record of the frame behind `frameUrl`. Read from media-ingest's
 * memory, so polling it costs nothing upstream.
 */
export const fetchFrameRecord = (cameraId: string) =>
  get<FrameRecord>(`/api/frame/${encodeURIComponent(cameraId)}/record`, 8000);

export const frameUrl = (cameraId: string) =>
  `${API_BASE}/api/frame/${encodeURIComponent(cameraId)}/latest.jpg`;

export const health = () =>
  get<{
    ok: boolean;
    graph: { junctions: number; blocks: number };
    media_ingest: { ok: boolean; why?: string };
  }>("/api/health", 3000);
