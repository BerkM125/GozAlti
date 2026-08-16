import type { StyleSpecification } from "maplibre-gl";

/**
 * The basemap style, written out rather than pulled from a vendor.
 *
 * Raster tiles were replaced with vector for two reasons: a raster tile is a
 * picture, so its colours cannot be matched to the UI, and it carries no
 * building geometry, so there is nothing to extrude. Vector tiles give exact
 * colour control and a `building` layer with per-feature heights.
 *
 * Tiles come from OpenFreeMap (OpenMapTiles schema, OSM data). No API key, no
 * account, no build step.
 */

const TILES = "https://tiles.openfreemap.org/planet";
const GLYPHS = "https://tiles.openfreemap.org/fonts/{fontstack}/{range}.pbf";

/** OpenFreeMap serves Noto Sans in Regular, Italic and Bold only. */
const FONT = ["Noto Sans Regular"];
const FONT_BOLD = ["Noto Sans Bold"];

/**
 * Map palette. Deliberately low-chroma: the interface is glass sitting on top
 * of this, and a busy basemap would fight the panels for attention. Roads are
 * white on a warm neutral ground, which is the Apple Maps convention and the
 * reason a route drawn on it reads instantly.
 */
export const MAP = {
  land: "#F5F4F1",
  water: "#AFD6ED",
  park: "#DEE8D5",
  wood: "#D8E4CF",
  sand: "#F0EAD8",

  buildingFlat: "#E4E0D6",
  buildingRoof: "#EFEBE1",
  buildingEdge: "#D8D3C7",

  roadCasing: "#E3E1DA",
  road: "#FFFFFF",
  motorway: "#F9DCAC",
  motorwayCasing: "#EFC57F",
  rail: "#DFDDD7",
  boundary: "#D5D2CA",

  label: "#57564F",
  labelHalo: "#FFFFFF",
  waterLabel: "#6D93AC",
} as const;

export const ATTRIBUTION =
  '© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors · ' +
  '<a href="https://openfreemap.org">OpenFreeMap</a> · cameras: City of Seattle / SDOT';

/** Road widths and text sizes, interpolated by zoom. */
const width = (stops: [number, number][]) =>
  ["interpolate", ["linear"], ["zoom"], ...stops.flat()] as never;

const IS_MOTORWAY = ["==", ["get", "class"], "motorway"];
const IS_MAJOR = ["in", ["get", "class"], ["literal", ["trunk", "primary"]]];
const IS_FOOT = ["in", ["get", "class"], ["literal", ["path", "pedestrian", "track"]]];

export function buildMapStyle(): StyleSpecification {
  return {
    version: 8,
    glyphs: GLYPHS,
    sources: {
      openmaptiles: { type: "vector", url: TILES, attribution: ATTRIBUTION },
    },
    layers: [
      { id: "background", type: "background", paint: { "background-color": MAP.land } },

      {
        id: "landcover",
        type: "fill",
        source: "openmaptiles",
        "source-layer": "landcover",
        paint: {
          "fill-color": [
            "match",
            ["get", "class"],
            "wood", MAP.wood,
            "grass", MAP.park,
            "sand", MAP.sand,
            MAP.park,
          ],
          "fill-opacity": 0.7,
        },
      },
      {
        id: "park",
        type: "fill",
        source: "openmaptiles",
        "source-layer": "park",
        paint: { "fill-color": MAP.park, "fill-opacity": 0.8 },
      },
      {
        id: "water",
        type: "fill",
        source: "openmaptiles",
        "source-layer": "water",
        paint: { "fill-color": MAP.water },
      },

      // Flat footprints. Swapped out for the extrusion in 3D so the two never
      // z-fight over the same geometry.
      {
        id: "building-flat",
        type: "fill",
        source: "openmaptiles",
        "source-layer": "building",
        minzoom: 13,
        layout: { visibility: "visible" },
        paint: {
          "fill-color": MAP.buildingFlat,
          "fill-opacity": ["interpolate", ["linear"], ["zoom"], 13, 0, 15, 1],
          "fill-outline-color": MAP.buildingEdge,
        },
      },

      // Roads: a casing pass under a fill pass, which is what gives streets a
      // drawn edge instead of a flat band.
      {
        id: "road-casing",
        type: "line",
        source: "openmaptiles",
        "source-layer": "transportation",
        filter: ["!", IS_FOOT] as never,
        layout: { "line-cap": "round", "line-join": "round" },
        paint: {
          "line-color": ["case", IS_MOTORWAY, MAP.motorwayCasing, MAP.roadCasing] as never,
          "line-width": [
            "interpolate", ["linear"], ["zoom"],
            10, ["case", IS_MOTORWAY, 3, 1.5],
            16, ["case", IS_MOTORWAY, 18, ["case", IS_MAJOR, 14, 10]],
            19, ["case", IS_MOTORWAY, 40, ["case", IS_MAJOR, 32, 24]],
          ] as never,
        },
      },
      {
        id: "road",
        type: "line",
        source: "openmaptiles",
        "source-layer": "transportation",
        filter: ["!", IS_FOOT] as never,
        layout: { "line-cap": "round", "line-join": "round" },
        paint: {
          "line-color": ["case", IS_MOTORWAY, MAP.motorway, MAP.road] as never,
          "line-width": [
            "interpolate", ["linear"], ["zoom"],
            10, ["case", IS_MOTORWAY, 2, 0.6],
            16, ["case", IS_MOTORWAY, 14, ["case", IS_MAJOR, 10.5, 7]],
            19, ["case", IS_MOTORWAY, 32, ["case", IS_MAJOR, 25, 18]],
          ] as never,
        },
      },
      // Footpaths matter to a walking app, so they are drawn rather than hidden.
      {
        id: "path",
        type: "line",
        source: "openmaptiles",
        "source-layer": "transportation",
        filter: IS_FOOT as never,
        minzoom: 14,
        paint: {
          "line-color": "#FFFFFF",
          "line-width": width([[14, 0.8], [19, 4]]),
          "line-dasharray": [2, 1.4],
        },
      },
      {
        id: "rail",
        type: "line",
        source: "openmaptiles",
        "source-layer": "transportation",
        filter: ["==", ["get", "class"], "rail"] as never,
        minzoom: 13,
        paint: { "line-color": MAP.rail, "line-width": width([[13, 0.6], [19, 2.5]]) },
      },

      // Extruded buildings. Hidden until the 3D toggle asks for them, which also
      // means MapLibre does no extrusion work in the default 2D view.
      {
        id: "building-3d",
        type: "fill-extrusion",
        source: "openmaptiles",
        "source-layer": "building",
        minzoom: 14,
        layout: { visibility: "none" },
        paint: {
          "fill-extrusion-color": MAP.buildingRoof,
          // render_height is null on buildings with no height or level tags;
          // 9 m is roughly a three-storey block and keeps the skyline plausible.
          "fill-extrusion-height": ["coalesce", ["get", "render_height"], 9] as never,
          "fill-extrusion-base": ["coalesce", ["get", "render_min_height"], 0] as never,
          "fill-extrusion-opacity": 0.94,
          // Shades walls darker than roofs automatically, which is what makes
          // the massing read as solid rather than as flat coloured shapes.
          "fill-extrusion-vertical-gradient": true,
        },
      },

      {
        id: "boundary",
        type: "line",
        source: "openmaptiles",
        "source-layer": "boundary",
        filter: ["<=", ["get", "admin_level"], 4] as never,
        paint: { "line-color": MAP.boundary, "line-width": 1, "line-dasharray": [3, 2] },
      },

      {
        id: "road-label",
        type: "symbol",
        source: "openmaptiles",
        "source-layer": "transportation_name",
        minzoom: 13,
        // Two exclusions, both noise on a walking map. Service roads and tracks
        // are parking aisles and driveways. Bare-number names are ferry berth
        // and lot markers ("34", "16", "41") that render as floating digits;
        // no Seattle street is named only with digits, so dropping anything
        // that parses as a number is safe here.
        filter: [
          "all",
          ["!", ["in", ["get", "class"], ["literal", ["service", "track"]]]],
          ["==", ["to-number", ["get", "name"], -999], -999],
        ] as never,
        layout: {
          "symbol-placement": "line",
          "text-field": ["get", "name"],
          "text-font": FONT,
          "text-size": width([[13, 9], [18, 13]]),
          "text-letter-spacing": 0.02,
        },
        paint: {
          "text-color": MAP.label,
          "text-halo-color": MAP.labelHalo,
          "text-halo-width": 1.6,
        },
      },
      {
        id: "water-label",
        type: "symbol",
        source: "openmaptiles",
        "source-layer": "water_name",
        layout: {
          "text-field": ["get", "name"],
          "text-font": FONT,
          "text-size": 12,
          "text-letter-spacing": 0.06,
        },
        paint: {
          "text-color": MAP.waterLabel,
          "text-halo-color": MAP.labelHalo,
          "text-halo-width": 1.2,
        },
      },
      {
        id: "place-label",
        type: "symbol",
        source: "openmaptiles",
        "source-layer": "place",
        filter: ["in", ["get", "class"], ["literal", ["city", "town", "suburb", "neighbourhood"]]] as never,
        layout: {
          "text-field": ["get", "name"],
          "text-font": FONT_BOLD,
          "text-size": [
            "match",
            ["get", "class"],
            "city", 15,
            "town", 13,
            11,
          ] as never,
          "text-letter-spacing": 0.04,
          "text-transform": "uppercase",
        },
        paint: {
          "text-color": MAP.label,
          "text-halo-color": MAP.labelHalo,
          "text-halo-width": 2,
        },
      },
    ],
  } as StyleSpecification;
}
