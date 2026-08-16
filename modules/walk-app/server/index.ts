/**
 * GozAlti walk-app server.
 *
 * One port (8020, the port SPEC.md §6.8 reserves for the frontend's single
 * backend) serving three things:
 *
 *   1. Pedestrian routing, computed here from the OSM block graph.
 *   2. A thin proxy to media-ingest (:8030) for cameras, frames, HLS and VLM
 *      detections. media-ingest owns every upstream SDOT call and its rate
 *      limits; this server never talks to SDOT directly.
 *   3. The built frontend in production.
 *
 * Everything that depends on media-ingest degrades to an explicit
 * `{ ok: false, why }` when it is not running, so the app stays usable with
 * routing alone.
 */

import { join } from "node:path";
import { existsSync } from "node:fs";
import { getGraph, type WalkGraph } from "./graph.ts";
import { NodeIndex, planRoute, type Algorithm } from "./routing.ts";
import { camerasAlongRoute, toConvergenceCamera, type LightCamera } from "./cameras.ts";

const ROOT = join(import.meta.dir, "..", "..", "..");
const DIST = join(import.meta.dir, "..", "dist");

const PORT = Number(Bun.env.PORT ?? 8020);
const INGEST = Bun.env.INGEST_BASE ?? "http://localhost:8030";

/** Downtown plus a margin, so a tap just outside still snaps to a street. */
const BBOX: [number, number, number, number] = [-122.375, 47.585, -122.29, 47.65];

/**
 * How far off the route a camera can sit and still be counted as watching it.
 * Downtown Seattle blocks run 80-120 m, so this reaches the cameras on the
 * route and those on the streets immediately either side of it.
 */
const ROUTE_CORRIDOR_M = Number(Bun.env.ROUTE_CORRIDOR_M ?? 180);

// ---------------------------------------------------------------------------
// Graph, built once at boot
// ---------------------------------------------------------------------------

const t0 = performance.now();
const graph: WalkGraph = getGraph({
  osmPath: join(ROOT, "experiments/surukamera/data/osm_ways.json"),
  bbox: BBOX,
  cachePath: join(import.meta.dir, "..", "data", "walk_graph.json"),
});
const index = new NodeIndex(graph);
const bootMs = Math.round(performance.now() - t0);
console.log(
  `walk graph ready: ${graph.nodes.size} junctions, ${graph.edges.size} blocks (${bootMs} ms)`,
);

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json", "cache-control": "no-store" },
  });

/** Parses "lat,lon" into GeoJSON order [lon, lat]. */
function parseLatLon(raw: string | null): [number, number] | null {
  if (!raw) return null;
  const parts = raw.split(",").map(Number);
  if (parts.length !== 2 || !parts.every(Number.isFinite)) return null;
  return [parts[1], parts[0]];
}

/**
 * Calls media-ingest. Never throws: a dead upstream is a normal state the UI
 * has to render, not a 500.
 */
async function ingest(path: string, timeoutMs = 4000): Promise<unknown> {
  try {
    const res = await fetch(`${INGEST}${path}`, {
      signal: AbortSignal.timeout(timeoutMs),
      headers: { "user-agent": "gozalti-walk-app/0.1" },
    });
    if (!res.ok) return { ok: false, why: `media-ingest returned ${res.status}` };
    return await res.json();
  } catch {
    return { ok: false, why: `media-ingest unreachable at ${INGEST}` };
  }
}

// ---------------------------------------------------------------------------
// Camera shape
//
// media-ingest serves the same cameras two ways. `/api/convergence` is the
// SPEC §6.7 contract shape, but its `live_hls` is the raw Wowza URL and it
// omits `key`, so a browser given that response would stream straight from
// SDOT - bypassing the module that owns every upstream rate limit (invariant
// #7, SPEC §6.9). `/api/nearby` carries `key` plus ready-made proxy-relative
// URLs and a server-computed `dist_m`, already sorted nearest first.
//
// So we consume `/api/nearby` and build §6.7 here, substituting our own
// same-origin proxy paths for the two URL fields. That substitution is the one
// documented deviation from §6.7's example (see SPEC.md): the contract says
// `live_hls` is a URL, null for snapshot-only cameras, and a proxied URL
// satisfies that while keeping every byte behind media-ingest.
// ---------------------------------------------------------------------------

/**
 * The whole camera set, cached.
 *
 * media-ingest answers this from its in-memory graph with no upstream call, so
 * the cost is one local request every few minutes. Holding it lets the route
 * corridor be computed against every camera at once instead of sampling the
 * polyline with a series of radius queries.
 */
const CAMERA_TTL_MS = 5 * 60_000;
let cameraCache: { at: number; cameras: LightCamera[] } | null = null;

async function allCameras(timeoutMs = 5000): Promise<LightCamera[] | { ok: false; why: string }> {
  if (cameraCache && Date.now() - cameraCache.at < CAMERA_TTL_MS) return cameraCache.cameras;
  const res = await ingest("/api/cameras", timeoutMs);
  if (!Array.isArray(res)) {
    // Serve a stale list rather than lose the layer to one bad request.
    if (cameraCache) return cameraCache.cameras;
    return res as { ok: false; why: string };
  }
  const cameras = (res as LightCamera[]).filter(
    (c) => c && typeof c.camera_id === "string" && Number.isFinite(c.lat) && Number.isFinite(c.lon),
  );
  cameraCache = { at: Date.now(), cameras };
  return cameras;
}

/**
 * Cameras near a point, or along a street, in §6.7 shape. Returns the upstream
 * `{ok:false, why}` untouched when media-ingest is down.
 */
async function cameraConvergence(
  query: { lat: string; lon: string; radius_m: string } | { street: string },
  timeoutMs = 4000,
): Promise<unknown> {
  if ("street" in query) {
    const res = await ingest(`/api/street/${encodeURIComponent(query.street)}`, timeoutMs);
    // This one returns a bare array, not an envelope.
    if (!Array.isArray(res)) return res;
    return { query, cameras: (res as LightCamera[]).map(toConvergenceCamera) };
  }
  const res = (await ingest(
    `/api/nearby?lat=${query.lat}&lon=${query.lon}&radius_m=${query.radius_m}`,
    timeoutMs,
  )) as { cameras?: LightCamera[] } | { ok: false; why: string };
  if (!res || !("cameras" in res) || !Array.isArray(res.cameras)) return res;
  return { query, cameras: res.cameras.map(toConvergenceCamera) };
}

/** Streams a binary upstream response (frames, HLS) straight through. */
async function ingestStream(path: string, timeoutMs = 8000): Promise<Response> {
  try {
    const res = await fetch(`${INGEST}${path}`, {
      signal: AbortSignal.timeout(timeoutMs),
      headers: { "user-agent": "gozalti-walk-app/0.1" },
    });
    if (!res.ok) return json({ ok: false, why: `media-ingest returned ${res.status}` }, 502);
    return new Response(res.body, {
      status: res.status,
      headers: {
        "content-type": res.headers.get("content-type") ?? "application/octet-stream",
        "cache-control": "no-store",
      },
    });
  } catch {
    return json({ ok: false, why: `media-ingest unreachable at ${INGEST}` }, 503);
  }
}

// ---------------------------------------------------------------------------
// Routes
// ---------------------------------------------------------------------------

async function handle(req: Request): Promise<Response> {
  const url = new URL(req.url);
  const path = url.pathname;

  // -- health ---------------------------------------------------------------
  if (path === "/api/health") {
    const upstream = (await ingest("/api/health", 1500)) as Record<string, unknown>;
    return json({
      ok: true,
      graph: { junctions: graph.nodes.size, blocks: graph.edges.size, bbox: BBOX },
      media_ingest: upstream?.ok === false ? upstream : { ok: true, base: INGEST },
    });
  }

  // -- routing --------------------------------------------------------------
  // POST { origin: [lon,lat], dest: [lon,lat], algorithm? }
  // GET  ?from=lat,lon&to=lat,lon&algorithm=
  if (path === "/api/route") {
    let origin: [number, number] | null = null;
    let dest: [number, number] | null = null;
    let algorithm: Algorithm = "astar";

    if (req.method === "POST") {
      try {
        const body = (await req.json()) as {
          origin?: [number, number];
          dest?: [number, number];
          algorithm?: Algorithm;
        };
        origin = body.origin ?? null;
        dest = body.dest ?? null;
        if (body.algorithm === "dijkstra") algorithm = "dijkstra";
      } catch {
        return json({ error: "malformed JSON body" }, 400);
      }
    } else {
      origin = parseLatLon(url.searchParams.get("from"));
      dest = parseLatLon(url.searchParams.get("to"));
      if (url.searchParams.get("algorithm") === "dijkstra") algorithm = "dijkstra";
    }

    if (!origin || !dest) {
      return json({ error: "need an origin and a destination" }, 400);
    }

    const result = planRoute(graph, index, origin, dest, algorithm);
    if ("error" in result) return json(result, 422);

    // Every camera that watches the recommended route, not just the ones near
    // its midpoint, in the order the walker passes them. A failure here must
    // not cost the user their route, so it degrades to an empty list.
    const cams = await allCameras(2500);
    if (Array.isArray(cams)) {
      result.safer.cameras_en_route = camerasAlongRoute(
        cams,
        result.safer.polyline,
        ROUTE_CORRIDOR_M,
      ).map((c) => c.camera_id);
    }
    return json(result);
  }

  // -- every camera watching a route (SPEC §6.7 shape) -----------------------
  // POST { polyline: [[lat,lon], ...], radius_m? }
  //
  // Separate from /api/cameras because a route is a line, not a point: asking
  // for cameras "near" one point on a 2 km walk is how the app ended up showing
  // the same three cameras regardless of where you were going.
  if (path === "/api/cameras/route" && req.method === "POST") {
    let polyline: [number, number][] = [];
    let radius = ROUTE_CORRIDOR_M;
    try {
      const body = (await req.json()) as { polyline?: [number, number][]; radius_m?: number };
      if (Array.isArray(body.polyline)) polyline = body.polyline;
      if (Number.isFinite(body.radius_m)) radius = Number(body.radius_m);
    } catch {
      return json({ error: "malformed JSON body" }, 400);
    }
    const clean = polyline.filter(
      (p) => Array.isArray(p) && p.length === 2 && p.every((n) => Number.isFinite(n)),
    );
    if (clean.length === 0) return json({ error: "need a polyline" }, 400);

    const cams = await allCameras();
    if (!Array.isArray(cams)) return json(cams);
    return json({
      query: { polyline_points: clean.length, radius_m: radius },
      cameras: camerasAlongRoute(cams, clean, radius),
    });
  }

  // -- cameras near a point (SPEC §6.7 CameraConvergence) --------------------
  if (path === "/api/cameras") {
    const street = url.searchParams.get("street");
    if (street) return json(await cameraConvergence({ street }));
    const lat = url.searchParams.get("lat");
    const lon = url.searchParams.get("lon");
    if (!lat || !lon) return json({ error: "need lat and lon, or a street" }, 400);
    const radius_m = url.searchParams.get("radius_m") ?? "400";
    return json(await cameraConvergence({ lat, lon, radius_m }));
  }

  // -- VLM detections for one camera (SPEC §6.2 Observation) -----------------
  const det = path.match(/^\/api\/detections\/(.+)$/);
  if (det) return json(await ingest(`/api/detections/${encodeURIComponent(det[1])}`, 8000));

  // -- a camera's latest frame ----------------------------------------------
  // 20 s, not the 8 s default. media-ingest buffers the whole upstream body
  // before answering and allows itself 20 s to get it, so a shorter budget
  // here aborts a frame it was still going to deliver - which shows up as an
  // empty tile, not as an error anyone can see.
  const frame = path.match(/^\/api\/frame\/(.+)\/latest\.jpg$/);
  if (frame) {
    return ingestStream(`/api/frame/${encodeURIComponent(frame[1])}/latest.jpg`, 20_000);
  }

  const record = path.match(/^\/api\/frame\/(.+)\/record$/);
  if (record) return json(await ingest(`/api/frame/${encodeURIComponent(record[1])}/record`));

  // -- HLS passthrough for live streams -------------------------------------
  // The path keeps its slash on purpose: media-ingest routes `{key}/{path}`,
  // and playlist URIs are relative, so proxying the playlist is what makes the
  // browser resolve chunklists and segments back through here too.
  // 20 s, not the 8 s default: media-ingest allows itself 20 s upstream, and
  // giving up first would 503 a segment it was still going to deliver.
  const hls = path.match(/^\/api\/hls\/(.+)$/);
  if (hls) return ingestStream(`/api/hls/${hls[1]}${url.search}`, 20_000);

  // -- live alerts (SPEC §6.5) over SSE --------------------------------------
  if (path === "/api/alerts/stream") return alertStream();

  if (path.startsWith("/api/")) return json({ error: "no such endpoint" }, 404);

  // -- static frontend (production) -----------------------------------------
  if (existsSync(DIST)) {
    const rel = path === "/" ? "/index.html" : path;
    const file = Bun.file(join(DIST, rel));
    if (await file.exists()) return new Response(file);
    // SPA fallback so a deep link still boots the app.
    const shell = Bun.file(join(DIST, "index.html"));
    if (await shell.exists()) return new Response(shell);
  }

  return new Response(
    "walk-app API is running. Run `bun run build` to serve the frontend from this port,\n" +
      "or `bun run dev` and open http://localhost:5173.\n",
    { status: 404, headers: { "content-type": "text/plain" } },
  );
}

// ---------------------------------------------------------------------------
// Alert stream
//
// Alerts are meant to come from synthesis (:8020 in the spec, not yet written).
// Until it exists this stream stays open and sends only heartbeats - an empty
// stream is honest, a fabricated alert is not.
// ---------------------------------------------------------------------------

function alertStream(): Response {
  const encoder = new TextEncoder();
  const body = new ReadableStream({
    start(controller) {
      const send = (event: string, data: unknown) =>
        controller.enqueue(encoder.encode(`event: ${event}\ndata: ${JSON.stringify(data)}\n\n`));

      send("hello", { ok: true, source: "walk-app", note: "no synthesis backend attached" });
      const beat = setInterval(() => {
        try {
          send("heartbeat", { at: new Date().toISOString() });
        } catch {
          clearInterval(beat);
        }
      }, 15_000);
    },
  });

  return new Response(body, {
    headers: {
      "content-type": "text/event-stream",
      "cache-control": "no-cache",
      connection: "keep-alive",
    },
  });
}

// ---------------------------------------------------------------------------

const server = Bun.serve({
  port: PORT,
  idleTimeout: 120,
  fetch: handle,
  error: (err) => {
    console.error(err);
    return json({ error: "internal error" }, 500);
  },
});

console.log(`walk-app listening on http://localhost:${server.port}`);
console.log(`media-ingest expected at ${INGEST}`);
