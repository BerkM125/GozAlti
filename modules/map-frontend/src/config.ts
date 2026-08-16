// The one line you edit when the network path changes.
//   tunnel   -> "https://<name>.trycloudflare.com"
//   lan      -> "http://192.168.1.42:8020"
//   same-origin (synthesis serving dist/) -> ""
// Per SPEC.md §6.8 the real backend is synthesis on :8020 — this module only
// mocks the Route contract until that service exists.
export const API_BASE = "";

// SPEC.md's practice: "mock all five contracts with fixture JSON on day one so
// UI work never blocks on backend readiness; swap to live endpoints behind one
// config constant." Flip to false for real routing — either against
// server/main.py (stopgap, :8020, see SPEC.md Quickstart) or synthesis once it
// exists.
export const USE_MOCK = false;

export const WALK_M_PER_MIN = 80;

export const BBOX: [number, number, number, number] = [-122.355, 47.595, -122.315, 47.625];
export const CENTER: [number, number] = [-122.336, 47.606];
export const DETOUR_CAP = 1.25;

// Local. Nothing here may point at the public internet.
export const TILES_URL = "pmtiles:///tiles/seattle-dt.pmtiles";
export const GLYPHS_URL = "/assets/fonts/{fontstack}/{range}.pbf";
// MapLibre v5 rejects a relative sprite URL outright ("must be absolute"), which
// kills the sprite load and every icon with it. Same-origin, so still local.
// Sprite name must match the flavor used in MapView (namedFlavor("black")).
export const SPRITE_URL = `${window.location.origin}/assets/sprites/black`;

export const COLORS = {
  safer: "#4FB9A5",
  direct: "#6E6E68",
  flagged: "#E0A33E",
  refusal: "#D2593F",
  origin: "#4FB9A5",
  dest: "#E0A33E",
};
