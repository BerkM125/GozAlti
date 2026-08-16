import { useEffect, useRef } from "react";
import maplibregl from "maplibre-gl";
import type { FeatureCollection } from "geojson";
import { CENTER, DEFAULT_ZOOM, PITCH_2D, PITCH_3D } from "../config.ts";
import { buildMapStyle, MAP } from "../mapStyle.ts";
import { familyOf, type Camera, type Detection, type LngLat, type RouteResult } from "../types.ts";

const EMPTY: FeatureCollection = { type: "FeatureCollection", features: [] };

const toLngLat = (line: [number, number][]): LngLat[] => line.map(([lat, lon]) => [lon, lat]);

type Props = {
  view: "2D" | "3D";
  result: RouteResult | null;
  origin: LngLat | null;
  dest: LngLat | null;
  cameras: Camera[];
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
  result,
  origin,
  dest,
  cameras,
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
  const cbs = useRef({ onMapTap, onSelectSegment, onSelectCamera, onUserLocation });
  cbs.current = { onMapTap, onSelectSegment, onSelectCamera, onUserLocation };

  const markers = useRef(new Map<string, maplibregl.Marker>());

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

      for (const id of ["direct", "safer", "segments"]) {
        m.addSource(id, { type: "geojson", data: EMPTY });
      }

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

      m.addLayer({
        id: "segment-selected",
        type: "line",
        source: "segments",
        layout: { "line-cap": "round", "line-join": "round" },
        paint: {
          "line-color": ["get", "flag"],
          "line-width": ["interpolate", ["linear"], ["zoom"], 12, 6, 17, 13],
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

      m.on("click", "segment-hit", (e) => {
        e.preventDefault();
        const id = e.features?.[0]?.properties?.segment_id as string | undefined;
        if (id) cbs.current.onSelectSegment(id);
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
      if (result) {
        m.fitBounds(routeBounds(result), { ...FIT_PADDING, pitch, duration: 700 });
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

  // -- routes --------------------------------------------------------------
  useEffect(() => {
    whenReady((m) => {
      paintRoutes(m, result);
      if (result) {
        m.fitBounds(routeBounds(result), {
          ...FIT_PADDING,
          duration: 700,
          pitch: view === "3D" ? PITCH_3D : PITCH_2D,
        });
      }
    });
  }, [result]);

  // -- selection -----------------------------------------------------------
  useEffect(() => {
    whenReady((m) =>
      m.setFilter("segment-selected", ["==", ["get", "segment_id"], selectedSegment ?? "__none__"]),
    );
  }, [selectedSegment]);

  // -- markers: origin, destination, cameras, detections -------------------
  useEffect(() => {
    whenReady((m) => {
      const want = new Map<string, { at: LngLat; node: HTMLElement; onClick?: () => void }>();

      if (origin) want.set("origin", { at: origin, node: el("mk mk-origin") });
      if (dest) want.set("dest", { at: dest, node: el("mk mk-dest") });

      for (const c of cameras) {
        const live = c.live_hls ? "mk-cam-live" : "";
        const sel = c.camera_id === selectedCamera ? "is-selected" : "";
        const node = el(`mk mk-cam ${live} ${sel}`, CAMERA_GLYPH);
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

      for (const { camera, detection } of detections) {
        if (!detection.est) continue; // no position estimate => not on the map
        const fam = familyOf(detection.label);
        const low = detection.conf < 0.5 ? "is-low" : "";
        want.set(`det:${camera.camera_id}:${detection.label}:${detection.cx}:${detection.cy}`, {
          at: [detection.est.lon, detection.est.lat],
          node: el(`mk mk-det mk-det-${fam} ${low}`),
        });
      }

      for (const [key, marker] of markers.current) {
        if (!want.has(key)) {
          marker.remove();
          markers.current.delete(key);
        }
      }
      for (const [key, spec] of want) {
        markers.current.get(key)?.remove();
        if (spec.onClick) {
          spec.node.addEventListener("click", (ev) => {
            ev.stopPropagation();
            spec.onClick!();
          });
        }
        const marker = new maplibregl.Marker({ element: spec.node, anchor: "center" })
          .setLngLat(spec.at)
          .addTo(m);
        markers.current.set(key, marker);
      }
    });
  }, [origin, dest, cameras, detections, selectedCamera]);

  return <div ref={container} className="map" />;
}

// ---------------------------------------------------------------------------

/** Leaves room for the top bar and the dock so the route is never behind them. */
const FIT_PADDING = { padding: { top: 110, bottom: 300, left: 44, right: 44 } };

function routeBounds(result: RouteResult): maplibregl.LngLatBounds {
  const b = new maplibregl.LngLatBounds();
  for (const c of toLngLat(result.safer.polyline)) b.extend(c);
  for (const c of toLngLat(result.shortest.polyline)) b.extend(c);
  return b;
}

/** Haze toward the horizon, so a tilted view has depth instead of a hard edge. */
const SKY = {
  "sky-color": "#C8DCEC",
  "horizon-color": "#EDEFF0",
  "fog-color": MAP.land,
  "fog-ground-blend": 0.55,
} as const;

/**
 * Route colours. Green is the recommendation and appears nowhere else on the
 * map; the direct route is grey because it is a reference, not a suggestion.
 * A white casing under the green keeps it readable over pale roads.
 */
const PALETTE = {
  safer: "#34C759",
  direct: "#8E8E93",
  flag: "#FF9500",
  casing: "#FFFFFF",
} as const;

const CAMERA_GLYPH =
  '<svg viewBox="0 0 24 24" width="13" height="13" aria-hidden="true">' +
  '<path fill="currentColor" d="M4 7h3l1.5-2h7L17 7h3a1 1 0 0 1 1 1v10a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V8a1 1 0 0 1 1-1Zm8 3.2A3.8 3.8 0 1 0 12 17.8 3.8 3.8 0 0 0 12 10.2Z"/>' +
  "</svg>";

function paintRoutes(m: maplibregl.Map, result: RouteResult | null) {
  const p = PALETTE;

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

  (m.getSource("direct") as maplibregl.GeoJSONSource)?.setData(
    line(result?.shortest.polyline, { color: p.direct }),
  );
  (m.getSource("safer") as maplibregl.GeoJSONSource)?.setData(
    line(result?.safer.polyline, { color: p.safer, casing: p.casing }),
  );
  (m.getSource("segments") as maplibregl.GeoJSONSource)?.setData(
    result
      ? ({
          type: "FeatureCollection",
          features: result.segments.map((s) => ({
            type: "Feature",
            properties: { segment_id: s.segment_id, flag: p.flag },
            geometry: s.geometry,
          })),
        } as FeatureCollection)
      : EMPTY,
  );
}
