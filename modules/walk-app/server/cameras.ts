/**
 * Which cameras watch a route.
 *
 * A single radius query around one point only ever finds the cameras near that
 * point, which is why the app used to show the same handful no matter how long
 * the walk was. What a walker actually wants is every camera that overlooks any
 * part of the way they are going, in the order they will pass them.
 *
 * media-ingest answers `GET /api/cameras` from its in-memory graph with all 646
 * cameras in ~30 ms and no upstream call, so the honest way to do this is to
 * take the whole set once and measure each camera against the real polyline,
 * rather than sampling the route with a series of radius queries that would
 * both miss cameras between samples and double-count the ones near them.
 */

/** One camera as media-ingest's light shape emits it (`ingest/service.py:_light`). */
export type LightCamera = {
  camera_id: string;
  key?: string | null;
  lat: number;
  lon: number;
  desc?: string | null;
  street?: string | null;
  has_stream?: boolean;
  bearing_deg?: number | null;
  bearing_conf?: number | null;
  active?: boolean | null;
  last_activity_at?: string | null;
  dist_m?: number | null;
};

export type ConvergenceCamera = {
  camera_id: string;
  lat: number;
  lon: number;
  bearing_deg: number | null;
  bearing_conf: number | null;
  live_hls: string | null;
  snapshot_url: string | null;
  distance_m: number | null;
  along_m: number | null;
  desc: string | null;
  street: string | null;
  active: boolean | null;
  last_activity_at: string | null;
};

/**
 * SPEC §6.7 camera, with the additive fields the walking UI needs.
 *
 * `live_hls` and `snapshot_url` are this server's own proxy paths, never the
 * upstream Wowza / seattle.gov URLs §6.7's example shows. A browser running
 * this bundle following an upstream URL would be this module calling SDOT
 * directly, which invariant #7 and SPEC §6.9 forbid. See SPEC.md.
 */
export function toConvergenceCamera(c: LightCamera): ConvergenceCamera {
  return {
    camera_id: c.camera_id,
    lat: c.lat,
    lon: c.lon,
    bearing_deg: c.bearing_deg ?? null,
    bearing_conf: c.bearing_conf ?? null,
    live_hls:
      c.has_stream && c.key ? `/api/hls/${encodeURIComponent(c.key)}/playlist.m3u8` : null,
    snapshot_url: `/api/frame/${encodeURIComponent(c.camera_id)}/latest.jpg`,
    distance_m: typeof c.dist_m === "number" ? c.dist_m : null,
    along_m: null,
    desc: c.desc ?? null,
    street: c.street ?? null,
    active: c.active ?? null,
    last_activity_at: c.last_activity_at ?? null,
  };
}

// ---------------------------------------------------------------------------
// Geometry
//
// Equirectangular projection about the route's own first point. Over a walk of
// a few kilometres the distortion is centimetres, and it makes the whole thing
// plain planar arithmetic instead of a haversine per segment.
// ---------------------------------------------------------------------------

const M_PER_DEG_LAT = 110_574;

type XY = { x: number; y: number };

function projector(originLat: number, originLon: number) {
  const mPerDegLon = 111_320 * Math.cos((originLat * Math.PI) / 180);
  return (lat: number, lon: number): XY => ({
    x: (lon - originLon) * mPerDegLon,
    y: (lat - originLat) * M_PER_DEG_LAT,
  });
}

/**
 * Closest point on segment a->b to p, as the distance to it and how far along
 * the segment it fell.
 */
function toSegment(p: XY, a: XY, b: XY): { dist: number; t: number; segLen: number } {
  const dx = b.x - a.x;
  const dy = b.y - a.y;
  const segLen = Math.hypot(dx, dy);
  if (segLen === 0) return { dist: Math.hypot(p.x - a.x, p.y - a.y), t: 0, segLen: 0 };
  // Clamped, so a camera beyond either end measures to the endpoint rather
  // than to an imaginary extension of the road.
  const t = Math.max(0, Math.min(1, ((p.x - a.x) * dx + (p.y - a.y) * dy) / (segLen * segLen)));
  const cx = a.x + t * dx;
  const cy = a.y + t * dy;
  return { dist: Math.hypot(p.x - cx, p.y - cy), t, segLen };
}

/**
 * Every camera within `radiusM` of the route, in the order the walker meets
 * them.
 *
 * `distance_m` becomes the perpendicular distance to the route - the useful
 * number, "how far off my way is this" - and `along_m` how far along the walk
 * the camera sits, which is what the ordering uses. A camera 30 m from the
 * final block sorts last even though it may be the closest one to the start.
 */
export function camerasAlongRoute(
  all: LightCamera[],
  polyline: [number, number][],
  radiusM: number,
): ConvergenceCamera[] {
  if (polyline.length === 0) return [];

  const project = projector(polyline[0][0], polyline[0][1]);
  const pts = polyline.map(([lat, lon]) => project(lat, lon));

  // Cumulative length to the start of each segment, so `along_m` is a real
  // distance walked rather than a segment index.
  const cum: number[] = [0];
  for (let i = 1; i < pts.length; i++) {
    cum.push(cum[i - 1] + Math.hypot(pts[i].x - pts[i - 1].x, pts[i].y - pts[i - 1].y));
  }

  const out: ConvergenceCamera[] = [];

  for (const cam of all) {
    if (!Number.isFinite(cam.lat) || !Number.isFinite(cam.lon)) continue;
    const p = project(cam.lat, cam.lon);

    let best = Infinity;
    let bestAlong = 0;
    for (let i = 1; i < pts.length; i++) {
      const { dist, t, segLen } = toSegment(p, pts[i - 1], pts[i]);
      if (dist < best) {
        best = dist;
        bestAlong = cum[i - 1] + t * segLen;
      }
    }
    // A one-point route has no segments; measure to the point itself.
    if (pts.length === 1) best = Math.hypot(p.x - pts[0].x, p.y - pts[0].y);

    if (best > radiusM) continue;
    out.push({
      ...toConvergenceCamera(cam),
      distance_m: Math.round(best * 10) / 10,
      along_m: Math.round(bestAlong * 10) / 10,
    });
  }

  out.sort((a, b) => (a.along_m ?? 0) - (b.along_m ?? 0));
  return out;
}
