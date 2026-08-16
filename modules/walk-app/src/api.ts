import { API_BASE } from "./config.ts";
import { isUnavailable } from "./types.ts";
import type {
  Convergence,
  CvResult,
  FrameRecord,
  LatLng,
  LivePathTick,
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
  // Only the safer trip starts a PathLiveSession upstream; the shortest one
  // is a static baseline and stays one-and-done.
  const live = kind === "safer";
  try {
    res = await fetch(
      `${API_BASE}/api/path?from=${origin[1]},${origin[0]}&to=${dest[1]},${dest[0]}&kind=${kind}&live=${live}`,
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
  const [saferRes, shortestRes] = await Promise.allSettled([
    fetchOnePath(origin, dest, "safer"),
    fetchOnePath(origin, dest, "shortest"),
  ]);
  // The safer call started a PathLiveSession upstream the moment it resolved.
  // On every exit that does not hand that session to the caller, stop it -
  // otherwise it ticks CV server-side for the full 180 s TTL with no reader.
  const saferPath =
    saferRes.status === "fulfilled" && !isUnavailable(saferRes.value) ? saferRes.value : null;
  const abandonSafer = () => {
    if (saferPath) stopLivePath(saferPath.path_id);
  };
  if (saferRes.status === "rejected") throw saferRes.reason;
  if (shortestRes.status === "rejected") {
    abandonSafer();
    throw shortestRes.reason;
  }
  if (isUnavailable(saferRes.value)) return saferRes.value;
  if (isUnavailable(shortestRes.value)) {
    abandonSafer();
    return shortestRes.value;
  }
  return { safer: saferRes.value, shortest: shortestRes.value };
}

/**
 * One PathLiveSession poll. "expired" means the session is gone upstream
 * (180 s TTL or explicit stop) and polling should cease; Unavailable is a
 * transient miss the caller should ride out without tearing anything down.
 */
export async function fetchLivePath(
  pathId: string,
  since: number,
): Promise<LivePathTick | "expired" | Unavailable> {
  let res: Response;
  try {
    res = await fetch(
      `${API_BASE}/api/path/live/${encodeURIComponent(pathId)}?since=${since}`,
      { signal: AbortSignal.timeout(8000) },
    );
  } catch {
    return { ok: false, why: "couldn't reach the walk-app server" };
  }
  if (res.status === 404) return "expired";
  if (!res.ok) return { ok: false, why: `live poll returned ${res.status}` };
  return (await res.json()) as LivePathTick;
}

/**
 * Fire-and-forget session stop. Failure is fine: the server expires the
 * session 180 s after the last poll anyway. `keepalive` lets the DELETE
 * survive a page refresh/close - without it the browser cancels the request
 * and the session keeps ticking CV server-side for the full 180 s TTL.
 */
export function stopLivePath(pathId: string): void {
  fetch(`${API_BASE}/api/path/live/${encodeURIComponent(pathId)}`, {
    method: "DELETE",
    keepalive: true,
    signal: AbortSignal.timeout(5000),
  }).catch(() => {});
}

/**
 * Local-CNN detections with world positions for one camera. The plain call
 * answers from media-ingest's cache in milliseconds and never triggers an
 * upstream fetch, so it is safe to poll fast; `backend:"detlib"` + force is
 * the one-shot HQ still pass and can take tens of seconds.
 */
export const fetchCameraCv = (
  cameraId: string,
  opts?: { backend?: "detlib" | "yolo"; force?: boolean },
): Promise<CvResult | Unavailable> => {
  const q = new URLSearchParams();
  if (opts?.backend) q.set("backend", opts.backend);
  if (opts?.force) q.set("force", "true");
  const qs = q.size ? `?${q}` : "";
  return get<CvResult>(
    `/api/cv/camera/${encodeURIComponent(cameraId)}${qs}`,
    opts?.force ? 50_000 : 10_000,
  );
};

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

/**
 * The consolidated router's static collision + osint weights as a second
 * heatmap. 503 (-> Unavailable) whenever the pathfinding artifacts are not
 * built on this box; the OSM weights layer is the always-working base.
 */
export const fetchRouterBlocks = () => get<FeatureCollection>("/api/blocks/router", 30_000);

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
