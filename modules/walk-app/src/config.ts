/** Same-origin in production; Vite proxies /api to the Bun server in dev. */
export const API_BASE = "";

/** Downtown Seattle. Matches the server's routing bbox. */
export const CENTER: [number, number] = [-122.3365, 47.6062];
export const DEFAULT_ZOOM = 14.6;

/** The tilt the 3D toggle flips to. Google Maps sits around here. */
export const PITCH_3D = 58;
export const PITCH_2D = 0;

export const WALK_M_PER_MIN = 80; // ~1.33 m/s

/**
 * How old a camera read may be before the UI stops treating it as current.
 * Mirrors media-ingest's ACTIVITY_MAX_AGE_S.
 */
export const FRESH_MAX_AGE_S = 300;

/**
 * Cameras shown when there is no route, ranked by distance from you.
 * With a route active there is no cap: every camera watching the way you are
 * going is shown, however long the walk.
 */
export const NEARBY_CAMERA_LIMIT = 12;
export const NEARBY_RADIUS_M = 500;

/**
 * How far off the route a camera may sit and still count as watching it.
 * Matches the server default; downtown blocks are 80-120 m, so this catches
 * the cameras on your street and on the ones immediately either side.
 */
export const ROUTE_CORRIDOR_M = 180;
