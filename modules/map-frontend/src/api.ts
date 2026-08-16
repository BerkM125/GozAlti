import { API_BASE, USE_MOCK } from "./config";
import type { LngLat, Refusal, Route, RoutePair } from "./types";
import { mockRoutes } from "./mockRouter";

export class RouteRefused extends Error {
  constructor(public detail: Refusal) {
    super(detail.message);
  }
}

const wait = (ms: number) => new Promise((r) => setTimeout(r, ms));

// TODO: path/method are provisional — synthesis (SPEC.md §6.8, :8020) owns the
// real route endpoint and hasn't defined its request shape yet. Coordinate
// before flipping USE_MOCK off.
async function fetchKind(origin: LngLat, dest: LngLat, kind: "shortest" | "safer"): Promise<Route> {
  const res = await fetch(`${API_BASE}/api/route`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ origin, dest, kind }),
  });
  if (res.status === 422) throw new RouteRefused(await res.json());
  if (!res.ok) throw new Error(`route failed: ${res.status}`);
  return res.json();
}

export async function getRoutes(origin: LngLat, dest: LngLat): Promise<RoutePair> {
  if (USE_MOCK) {
    await wait(350);
    return mockRoutes(origin, dest);
  }
  const [shortest, safer] = await Promise.all([
    fetchKind(origin, dest, "shortest"),
    fetchKind(origin, dest, "safer"),
  ]);
  return { shortest, safer };
}

export async function health(): Promise<boolean> {
  if (USE_MOCK) return true;
  try {
    const r = await fetch(`${API_BASE}/health`, { signal: AbortSignal.timeout(2500) });
    return r.ok;
  } catch {
    return false;
  }
}
