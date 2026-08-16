import { API_BASE } from "./config.ts";
import type {
  Convergence,
  FrameRecord,
  LatLng,
  LngLat,
  Observation,
  RouteResult,
  Unavailable,
} from "./types.ts";

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
