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

const ROOT = join(import.meta.dir, "..", "..", "..");
const DIST = join(import.meta.dir, "..", "dist");

const PORT = Number(Bun.env.PORT ?? 8020);
const INGEST = Bun.env.INGEST_BASE ?? "http://localhost:8030";

/** Downtown plus a margin, so a tap just outside still snaps to a street. */
const BBOX: [number, number, number, number] = [-122.375, 47.585, -122.29, 47.65];

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

    // Cameras that watch the safer route, if media-ingest is up. A failure here
    // must not cost the user their route, so it degrades to an empty list.
    const mid = result.safer.polyline[Math.floor(result.safer.polyline.length / 2)];
    if (mid) {
      const conv = (await ingest(
        `/api/convergence?lat=${mid[0]}&lon=${mid[1]}&radius_m=400`,
        2500,
      )) as { cameras?: { camera_id: string }[] };
      if (Array.isArray(conv?.cameras)) {
        result.safer.cameras_en_route = conv.cameras.map((c) => c.camera_id);
      }
    }
    return json(result);
  }

  // -- cameras near a point (SPEC §6.7 CameraConvergence) --------------------
  if (path === "/api/cameras") {
    const lat = url.searchParams.get("lat");
    const lon = url.searchParams.get("lon");
    const radius = url.searchParams.get("radius_m") ?? "400";
    const street = url.searchParams.get("street");
    const qs = street
      ? `street=${encodeURIComponent(street)}`
      : `lat=${lat}&lon=${lon}&radius_m=${radius}`;
    return json(await ingest(`/api/convergence?${qs}`));
  }

  // -- VLM detections for one camera (SPEC §6.2 Observation) -----------------
  const det = path.match(/^\/api\/detections\/(.+)$/);
  if (det) return json(await ingest(`/api/detections/${encodeURIComponent(det[1])}`, 8000));

  // -- a camera's latest frame ----------------------------------------------
  const frame = path.match(/^\/api\/frame\/(.+)\/latest\.jpg$/);
  if (frame) return ingestStream(`/api/frame/${encodeURIComponent(frame[1])}/latest.jpg`);

  const record = path.match(/^\/api\/frame\/(.+)\/record$/);
  if (record) return json(await ingest(`/api/frame/${encodeURIComponent(record[1])}/record`));

  // -- HLS passthrough for live streams -------------------------------------
  const hls = path.match(/^\/api\/hls\/(.+)$/);
  if (hls) return ingestStream(`/api/hls/${hls[1]}`);

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
