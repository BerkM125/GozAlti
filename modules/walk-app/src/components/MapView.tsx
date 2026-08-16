import { useEffect, useRef } from "react";
import maplibregl from "maplibre-gl";
import type { FeatureCollection } from "geojson";
import { CENTER, DEFAULT_ZOOM, PITCH_2D, PITCH_3D } from "../config.ts";
import { buildMapStyle } from "../mapStyle.ts";
import { ROUTE, SELECTED, SKY, WEIGHT_STOPS } from "../palette.ts";
import { fetchBlocks } from "../api.ts";
import {
  familyOf,
  isUnavailable,
  type Camera,
  type Detection,
  type LngLat,
  type PathPair,
  type PathRefuge,
  type RouteResult,
} from "../types.ts";
import { CAMERA_MARKER_GLYPH } from "./icons.tsx";

const EMPTY: FeatureCollection = { type: "FeatureCollection", features: [] };

const toLngLat = (line: [number, number][]): LngLat[] => line.map(([lat, lon]) => [lon, lat]);

type Props = {
  view: "2D" | "3D";
  /** "weights" paints every block by its routing weight; "plain" hides it. */
  layer: "weights" | "plain";
  /** True while the search bar's "Choose on the map" waits for a tap. Block
   *  taps yield to it, so the tap places the stop instead of opening a sheet. */
  pickingStop: boolean;
  /** Local fallback router result. Null whenever `path` is set. */
  result: RouteResult | null;
  /** Consolidated router (modules/pathfinding) result. Wins over `result`. */
  path: PathPair | null;
  origin: LngLat | null;
  dest: LngLat | null;
  /** Where the walker is, when known. Drawn as its own marker. */
  userPos: LngLat | null;
  cameras: Camera[];
  /** Camera ids on the active path, drawn with the en-route ring. */
  routeCamIds: Set<string>;
  /** Open businesses along the path — the "exit route" dots. */
  refuges: PathRefuge[];
  detections: { camera: Camera; detection: Detection }[];
  selectedSegment: string | null;
  selectedCamera: string | null;
  onSelectSegment: (id: string | null) => void;
  onSelectCamera: (id: string) => void;
  onMapTap: (p: LngLat) => void;
  onUserLocation: (p: LngLat) => void;
};

/** Builds a DOM node for a marker; MapLibre symbol layers need glyphs we don't ship. */
function el(className: string, html = ""): HTMLElement {
  const node = document.createElement("div");
  node.className = className;
  node.innerHTML = html;
  return node;
}

export default function MapView({
  view,
  layer,
  pickingStop,
  result,
  path,
  origin,
  dest,
  userPos,
  cameras,
  routeCamIds,
  refuges,
  detections,
  selectedSegment,
  selectedCamera,
  onSelectSegment,
  onSelectCamera,
  onMapTap,
  onUserLocation,
}: Props) {
  const container = useRef<HTMLDivElement>(null);
  const map = useRef<maplibregl.Map | null>(null);
  const ready = useRef(false);

  // Callbacks live in refs so the map is created exactly once and never has to
  // be torn down when a parent re-renders.
  const cbs = useRef({ onMapTap, onSelectSegment, onSelectCamera, onUserLocation, pickingStop });
  cbs.current = { onMapTap, onSelectSegment, onSelectCamera, onUserLocation, pickingStop };

  // Keyed markers plus the HTML they were built from, so a re-render can tell
  // "same marker, maybe moved" from "this marker actually changed". Tearing
  // down and recreating every marker on each pass made them all flicker and
  // re-stack whenever anything - a camera refetch, a location tick - changed.
  const markers = useRef(new Map<string, { marker: maplibregl.Marker; sig: string }>());

  // -- create once ---------------------------------------------------------
  useEffect(() => {
    if (map.current || !container.current) return;

    const m = new maplibregl.Map({
      container: container.current,
      center: CENTER,
      zoom: DEFAULT_ZOOM,
      pitch: PITCH_2D,
      maxPitch: 70,
      attributionControl: { compact: true },
      // A pitched map is only legible if you can also turn it.
      dragRotate: true,
      style: buildMapStyle(),
    });
    map.current = m;

    // A map that fails silently looks identical to a map with nothing on it.
    m.on("error", (e) => console.error("[map]", e.error?.message ?? e));
    // Reachable from the console for on-device debugging during the demo.
    (window as unknown as { __map: maplibregl.Map }).__map = m;

    const geo = new maplibregl.GeolocateControl({
      positionOptions: { enableHighAccuracy: true },
      trackUserLocation: true,
    });
    m.addControl(geo, "bottom-right");
    geo.on("geolocate", (e: GeolocationPosition) => {
      cbs.current.onUserLocation([e.coords.longitude, e.coords.latitude]);
    });

    m.on("load", () => {
      m.setSky(SKY);

      for (const id of ["blocks", "direct", "safer", "segments"]) {
        m.addSource(id, { type: "geojson", data: EMPTY });
      }

      // Every walkable block, coloured by routing weight. Under the road
      // labels (beforeId), so street names stay readable over the ramp.
      m.addLayer(
        {
          id: "blocks-heat",
          type: "line",
          source: "blocks",
          layout: { "line-cap": "round", "line-join": "round" },
          paint: {
            "line-color": [
              "interpolate",
              ["linear"],
              ["get", "risk"],
              ...WEIGHT_STOPS.flat(),
            ],
            "line-width": ["interpolate", ["linear"], ["zoom"], 12, 1.5, 14, 2.75, 16, 5, 18, 9],
            "line-opacity": 0.85,
          },
        },
        "road-label",
      );

      // Tap target for any block. Narrower than the route's 30px so a tap
      // beside a street still drops a routing pin instead of opening evidence.
      m.addLayer({
        id: "blocks-hit",
        type: "line",
        source: "blocks",
        paint: { "line-color": "#000", "line-width": 22, "line-opacity": 0 },
      });

      // The weight data is static per server boot; one fetch fills the layer.
      fetchBlocks().then((fc) => {
        if (isUnavailable(fc)) {
          console.error("[map] weight layer unavailable:", fc.why);
          return;
        }
        (m.getSource("blocks") as maplibregl.GeoJSONSource)?.setData(fc);
      });

      // The plain shortest walk: present, but visually quiet.
      m.addLayer({
        id: "direct-line",
        type: "line",
        source: "direct",
        layout: { "line-cap": "round", "line-join": "round" },
        paint: {
          "line-color": ["get", "color"],
          "line-width": ["interpolate", ["linear"], ["zoom"], 12, 2.5, 17, 5],
          "line-dasharray": [2, 1.8],
          "line-opacity": 0.75,
        },
      });

      // A white casing under the recommended route keeps it legible over pale
      // roads without shifting its hue.
      m.addLayer({
        id: "safer-casing",
        type: "line",
        source: "safer",
        layout: { "line-cap": "round", "line-join": "round" },
        paint: {
          "line-color": ["get", "casing"],
          "line-width": ["interpolate", ["linear"], ["zoom"], 12, 7, 17, 13],
          "line-opacity": 0.9,
        },
      });
      m.addLayer({
        id: "safer-line",
        type: "line",
        source: "safer",
        layout: { "line-cap": "round", "line-join": "round" },
        paint: {
          "line-color": ["get", "color"],
          "line-width": ["interpolate", ["linear"], ["zoom"], 12, 4, 17, 9],
        },
      });

      // The tapped block, white-cased dark blue so it reads over both the
      // weight ramp and the route. Drawn from the blocks source because that
      // holds every edge, so tap-anywhere selection works off-route too.
      m.addLayer({
        id: "block-selected-casing",
        type: "line",
        source: "blocks",
        layout: { "line-cap": "round", "line-join": "round" },
        paint: {
          "line-color": SELECTED.casing,
          "line-width": ["interpolate", ["linear"], ["zoom"], 12, 9, 17, 16],
        },
        filter: ["==", ["get", "segment_id"], "__none__"],
      });
      m.addLayer({
        id: "block-selected",
        type: "line",
        source: "blocks",
        layout: { "line-cap": "round", "line-join": "round" },
        paint: {
          "line-color": SELECTED.line,
          "line-width": ["interpolate", ["linear"], ["zoom"], 12, 5, 17, 11],
        },
        filter: ["==", ["get", "segment_id"], "__none__"],
      });

      // The tapped consolidated-router segment, same visual language. Its
      // pf:* segment_ids live in the segments source, not in blocks, so the
      // block-selected filter can never match them.
      m.addLayer({
        id: "segment-selected-casing",
        type: "line",
        source: "segments",
        layout: { "line-cap": "round", "line-join": "round" },
        paint: {
          "line-color": SELECTED.casing,
          "line-width": ["interpolate", ["linear"], ["zoom"], 12, 9, 17, 16],
        },
        filter: ["==", ["get", "segment_id"], "__none__"],
      });
      m.addLayer({
        id: "segment-selected",
        type: "line",
        source: "segments",
        layout: { "line-cap": "round", "line-join": "round" },
        paint: {
          "line-color": SELECTED.line,
          "line-width": ["interpolate", ["linear"], ["zoom"], 12, 5, 17, 11],
        },
        filter: ["==", ["get", "segment_id"], "__none__"],
      });

      // A 9px stroke is not a thumb target.
      m.addLayer({
        id: "segment-hit",
        type: "line",
        source: "segments",
        paint: { "line-color": "#000", "line-width": 30, "line-opacity": 0 },
      });

      // Click handlers fire in registration order, which is the precedence:
      // a route segment first, then any block, then the bare-map tap.
      m.on("click", "segment-hit", (e) => {
        // "Choose on the map" is an explicit promise that the next tap places
        // a stop - it outranks even a route segment's evidence sheet.
        if (cbs.current.pickingStop) return;
        e.preventDefault();
        const id = e.features?.[0]?.properties?.segment_id as string | undefined;
        if (id) cbs.current.onSelectSegment(id);
      });

      m.on("click", "blocks-hit", (e) => {
        if (e.defaultPrevented) return; // a route segment already took the tap
        // "Choose on the map" is an explicit promise that the next tap places
        // a stop; falling through lets the bare-map handler keep it.
        if (cbs.current.pickingStop) return;
        const id = e.features?.[0]?.properties?.segment_id as string | undefined;
        if (!id) return;
        e.preventDefault();
        cbs.current.onSelectSegment(id);
      });

      m.on("click", (e) => {
        if (e.defaultPrevented) return;
        cbs.current.onSelectSegment(null);
        cbs.current.onMapTap([e.lngLat.lng, e.lngLat.lat]);
      });

      ready.current = true;
      m.fire("app:ready");
    });

    return () => {
      m.remove();
      map.current = null;
      ready.current = false;
    };
  }, []);

  /** Runs `fn` now if the style is loaded, otherwise once it is. */
  const whenReady = (fn: (m: maplibregl.Map) => void) => {
    const m = map.current;
    if (!m) return;
    if (ready.current) fn(m);
    else m.once("app:ready", () => fn(m));
  };

  // -- 2D / 3D -------------------------------------------------------------
  useEffect(() => {
    whenReady((m) => {
      const is3D = view === "3D";
      const pitch = is3D ? PITCH_3D : PITCH_2D;

      // Swap flat footprints for extrusions rather than drawing both: they are
      // the same geometry, so overlaying them z-fights. Hiding the extrusion in
      // 2D also means MapLibre does no extrusion work in the default view.
      m.setLayoutProperty("building-flat", "visibility", is3D ? "none" : "visible");
      m.setLayoutProperty("building-3d", "visibility", is3D ? "visible" : "none");

      // Extrusions only exist from z14, so a tilted view above that zoom shows
      // a flat city and looks broken.
      const zoom = is3D ? Math.max(m.getZoom(), 15) : m.getZoom();

      // With a route on screen, tilting must keep the whole route framed -
      // zooming in on the tilt is what pushes the destination off the edge.
      if (result || path) {
        m.fitBounds(routeBounds(result, path), { ...FIT_PADDING, pitch, duration: 700 });
        return;
      }
      m.easeTo({
        pitch,
        zoom,
        // Coming back to 2D squares the map up, the way Google Maps does.
        bearing: is3D ? m.getBearing() : 0,
        duration: 700,
      });
    });
  }, [view]);

  // -- weight layer on/off -------------------------------------------------
  useEffect(() => {
    whenReady((m) => {
      const vis = layer === "weights" ? "visible" : "none";
      m.setLayoutProperty("blocks-heat", "visibility", vis);
      // Plain mode also restores tap-to-route everywhere: no hit layer, no
      // accidental evidence sheets.
      m.setLayoutProperty("blocks-hit", "visibility", vis);
    });
  }, [layer]);

  // -- routes --------------------------------------------------------------
  useEffect(() => {
    whenReady((m) => {
      paintRoutes(m, result, path);
      // With a route up, the weights recede to context so the route pops.
      // MapLibre's default 300ms paint transition animates the change.
      m.setPaintProperty("blocks-heat", "line-opacity", result || path ? 0.3 : 0.85);
      if (result || path) {
        m.fitBounds(routeBounds(result, path), {
          ...FIT_PADDING,
          duration: 700,
          pitch: view === "3D" ? PITCH_3D : PITCH_2D,
        });
      }
    });
  }, [result, path]);

  // -- centre on the walker, once ------------------------------------------
  // Only the first fix moves the camera. Re-centring on every update would
  // fight the user's own panning, and a framed route must never be yanked out
  // of view by a late position.
  const flown = useRef(false);
  useEffect(() => {
    if (!userPos || flown.current) return;
    flown.current = true;
    whenReady((m) => {
      if (result || path) return;
      m.easeTo({ center: userPos, zoom: Math.max(m.getZoom(), 15.5), duration: 900 });
    });
  }, [userPos, result, path]);

  // -- selection -----------------------------------------------------------
  useEffect(() => {
    whenReady((m) => {
      const filter: maplibregl.FilterSpecification = [
        "==",
        ["get", "segment_id"],
        selectedSegment ?? "__none__",
      ];
      m.setFilter("block-selected-casing", filter);
      m.setFilter("block-selected", filter);
      m.setFilter("segment-selected-casing", filter);
      m.setFilter("segment-selected", filter);
    });
  }, [selectedSegment]);

  // -- markers: origin, destination, cameras, detections -------------------
  useEffect(() => {
    whenReady((m) => {
      const want = new Map<string, { at: LngLat; node: HTMLElement; onClick?: () => void }>();

      // Drawn ourselves because the position now arrives from
      // getCurrentPosition on load, not only from the GeolocateControl, and
      // the control only paints its dot once it has been tapped.
      if (userPos) want.set("user", { at: userPos, node: el("mk mk-user") });

      if (origin) want.set("origin", { at: origin, node: el("mk mk-origin") });
      if (dest) want.set("dest", { at: dest, node: el("mk mk-dest") });

      // Camera markers belong to a planned route: the corridor query is what
      // gives them meaning ("these watch your way"). With no route, the map
      // stays clean - the panel still lists what is near you - except for a
      // camera the user has actually opened, which gets its marker so the
      // viewer's frame can be placed on the street.
      const mapCameras =
        result || path ? cameras : cameras.filter((c) => c.camera_id === selectedCamera);

      for (const c of mapCameras) {
        const live = c.live_hls ? "mk-cam-live" : "";
        const sel = c.camera_id === selectedCamera ? "is-selected" : "";
        const enRoute = routeCamIds.has(c.camera_id) ? "is-route" : "";
        const node = el(`mk mk-cam ${live} ${sel} ${enRoute}`, CAMERA_MARKER_GLYPH);
        // A cone is drawn only when the bearing is actually resolved. An
        // unresolved camera shows a marker with no direction rather than a
        // guessed one.
        if (c.bearing_deg !== null && c.bearing_conf !== null) {
          const cone = document.createElement("span");
          cone.className = "mk-cone";
          cone.style.setProperty("--rot", `${c.bearing_deg}deg`);
          cone.style.setProperty("--conf", String(Math.max(0.12, Math.min(0.5, c.bearing_conf))));
          node.appendChild(cone);
        }
        want.set(`cam:${c.camera_id}`, {
          at: [c.lon, c.lat],
          node,
          onClick: () => cbs.current.onSelectCamera(c.camera_id),
        });
      }

      // Open businesses along the path. Green because they are part of the
      // recommendation — an open door to walk toward (demo-ui checklist row 6).
      for (const r of refuges) {
        const node = el("mk mk-refuge");
        node.title = `${r.name ?? "open business"}${r.open_until ? ` · open until ${r.open_until}` : " · open now"}`;
        want.set(`refuge:${r.osm_id ?? `${r.lat},${r.lon}`}`, { at: [r.lon, r.lat], node });
      }

      for (const { camera, detection } of detections) {
        if (!detection.est) continue; // no position estimate => not on the map
        const fam = familyOf(detection.label);
        const low = detection.conf < 0.5 ? "is-low" : "";
        want.set(`det:${camera.camera_id}:${detection.label}:${detection.cx}:${detection.cy}`, {
          at: [detection.est.lon, detection.est.lat],
          node: el(`mk mk-det mk-det-${fam} ${low}`),
        });
      }

      for (const [key, entry] of markers.current) {
        if (!want.has(key)) {
          entry.marker.remove();
          markers.current.delete(key);
        }
      }
      for (const [key, spec] of want) {
        // The signature is read before MapLibre decorates the element with its
        // own classes and inline transform, so it describes only what we built.
        const sig = spec.node.outerHTML;
        const existing = markers.current.get(key);
        if (existing && existing.sig === sig) {
          existing.marker.setLngLat(spec.at); // same marker, possibly moved
          continue;
        }
        existing?.marker.remove();
        if (spec.onClick) {
          spec.node.addEventListener("click", (ev) => {
            ev.stopPropagation();
            spec.onClick!();
          });
        }
        const marker = new maplibregl.Marker({ element: spec.node, anchor: "center" })
          .setLngLat(spec.at)
          .addTo(m);
        markers.current.set(key, { marker, sig });
      }
    });
  }, [origin, dest, userPos, cameras, detections, selectedCamera, result, path, refuges, routeCamIds]);

  return <div ref={container} className="map" />;
}

// ---------------------------------------------------------------------------

/** Leaves room for the top bar and the dock so the route is never behind them. */
const TOP_CHROME_PX = 110; /* topbar plus safe-area headroom */
const BOTTOM_CHROME_PX = 300; /* the dock at its full routed height */
const FIT_PADDING = {
  padding: { top: TOP_CHROME_PX, bottom: BOTTOM_CHROME_PX, left: 44, right: 44 },
};

function routeBounds(result: RouteResult | null, path: PathPair | null): maplibregl.LngLatBounds {
  const b = new maplibregl.LngLatBounds();
  const lines = path
    ? [path.safer.polyline, path.shortest.polyline]
    : result
      ? [result.safer.polyline, result.shortest.polyline]
      : [];
  for (const line of lines) for (const c of toLngLat(line)) b.extend(c);
  return b;
}

/**
 * Consolidated-router segments wear their risk bucket (demo-ui checklist
 * row 4): the recommendation green, flagged orange, alert red. Red on a
 * segment is the one sanctioned extension of the colour budget — it marks a
 * high live/collision weighting, the thing the demo exists to show. Every
 * bucket links back to `risk_parts` + `risk_basis` in the segment sheet.
 */
const BUCKET = {
  low: "#34C759",
  medium: "#FF9500",
  high: "#FF3B30",
} as const;

function paintRoutes(m: maplibregl.Map, result: RouteResult | null, path: PathPair | null) {
  const p = ROUTE;

  const line = (polyline: [number, number][] | undefined, props: Record<string, string>) =>
    polyline
      ? ({
          type: "FeatureCollection",
          features: [
            {
              type: "Feature",
              properties: props,
              geometry: { type: "LineString", coordinates: toLngLat(polyline) },
            },
          ],
        } as FeatureCollection)
      : EMPTY;

  const src = (id: string) => m.getSource(id) as maplibregl.GeoJSONSource | undefined;

  if (path) {
    // Consolidated router: the recommended route is its segments, each wearing
    // its risk bucket. The existing safer-line layer reads per-feature colour,
    // so one feature per segment is all it takes.
    src("direct")?.setData(line(path.shortest.polyline, { color: p.direct }));
    src("safer")?.setData({
      type: "FeatureCollection",
      features: path.safer.segments.map((s) => ({
        type: "Feature",
        properties: { color: BUCKET[s.risk_bucket] ?? p.safer, casing: p.casing },
        geometry: s.geometry,
      })),
    } as FeatureCollection);
    src("segments")?.setData({
      type: "FeatureCollection",
      features: path.safer.segments.map((s) => ({
        type: "Feature",
        properties: { segment_id: s.segment_id },
        geometry: s.geometry,
      })),
    } as FeatureCollection);
    return;
  }

  src("direct")?.setData(line(result?.shortest.polyline, { color: p.direct }));
  src("safer")?.setData(line(result?.safer.polyline, { color: p.safer, casing: p.casing }));
  src("segments")?.setData(
    result
      ? ({
          type: "FeatureCollection",
          features: result.segments.map((s) => ({
            type: "Feature",
            properties: { segment_id: s.segment_id },
            geometry: s.geometry,
          })),
        } as FeatureCollection)
      : EMPTY,
  );
}
