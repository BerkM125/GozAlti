import { useEffect, useRef } from "react";
import maplibregl from "maplibre-gl";
import { layers, namedFlavor } from "@protomaps/basemaps";
import { CENTER, COLORS, GLYPHS_URL, SPRITE_URL, TILES_URL } from "../config";
import type { LatLng, LngLat, RoutePair } from "../types";

const EMPTY = { type: "FeatureCollection", features: [] } as GeoJSON.FeatureCollection;

const toLngLat = (polyline: LatLng[]): LngLat[] => polyline.map(([lat, lon]) => [lon, lat]);

function segmentsFC(routes: RoutePair | null): GeoJSON.FeatureCollection {
  if (!routes?.safer.segments) return EMPTY;
  return {
    type: "FeatureCollection",
    features: routes.safer.segments.map((s) => ({
      type: "Feature",
      id: s.segment_id,
      properties: { segment_id: s.segment_id, base_risk: s.base_risk },
      geometry: s.geometry,
    })),
  };
}

function lineFC(polyline?: LatLng[]): GeoJSON.FeatureCollection {
  if (!polyline) return EMPTY;
  return {
    type: "FeatureCollection",
    features: [
      { type: "Feature", properties: {}, geometry: { type: "LineString", coordinates: toLngLat(polyline) } },
    ],
  };
}

type Props = {
  routes: RoutePair | null;
  origin: LngLat | null;
  dest: LngLat | null;
  selected: string | null;
  onSelect: (id: string | null) => void;
  onMapTap: (p: LngLat) => void;
};

export default function MapView({ routes, origin, dest, selected, onSelect, onMapTap }: Props) {
  const container = useRef<HTMLDivElement>(null);
  const map = useRef<maplibregl.Map | null>(null);
  const ready = useRef(false);
  const tapRef = useRef(onMapTap);
  const selectRef = useRef(onSelect);
  tapRef.current = onMapTap;
  selectRef.current = onSelect;

  useEffect(() => {
    if (map.current || !container.current) return;

    const m = new maplibregl.Map({
      container: container.current,
      center: CENTER,
      zoom: 14.2,
      attributionControl: { compact: true },
      style: {
        version: 8,
        glyphs: GLYPHS_URL,
        sprite: SPRITE_URL,
        sources: {
          protomaps: {
            type: "vector",
            url: TILES_URL,
            attribution: 'Protomaps © OpenStreetMap',
          },
        },
        layers: layers("protomaps", namedFlavor("black"), { lang: "en" }),
      },
    });
    map.current = m;

    m.addControl(new maplibregl.GeolocateControl({ trackUserLocation: false }), "top-right");

    m.on("load", () => {
      m.addSource("direct", { type: "geojson", data: EMPTY });
      m.addSource("safer", { type: "geojson", data: EMPTY });
      m.addSource("segments", { type: "geojson", data: EMPTY });
      m.addSource("pins", { type: "geojson", data: EMPTY });

      m.addLayer({
        id: "direct-line",
        type: "line",
        source: "direct",
        paint: {
          "line-color": COLORS.direct,
          "line-width": 3,
          "line-dasharray": [2, 1.6],
          "line-opacity": 0.85,
        },
      });

      m.addLayer({
        id: "safer-line",
        type: "line",
        source: "safer",
        layout: { "line-cap": "round", "line-join": "round" },
        paint: { "line-color": COLORS.safer, "line-width": 5 },
      });

      m.addLayer({
        id: "segment-selected",
        type: "line",
        source: "segments",
        layout: { "line-cap": "round" },
        paint: { "line-color": COLORS.flagged, "line-width": 7 },
        filter: ["==", ["get", "segment_id"], "__none__"],
      });

      // Invisible fat line: a 5px stroke is not a thumb target.
      m.addLayer({
        id: "segment-hit",
        type: "line",
        source: "segments",
        paint: { "line-color": "#000", "line-width": 26, "line-opacity": 0 },
      });

      m.addLayer({
        id: "pin-dots",
        type: "circle",
        source: "pins",
        paint: {
          "circle-radius": 7,
          "circle-color": ["get", "color"],
          "circle-stroke-width": 2,
          "circle-stroke-color": "#0E100E",
        },
      });

      m.on("click", "segment-hit", (e) => {
        e.preventDefault();
        const id = e.features?.[0]?.properties?.segment_id as string | undefined;
        if (id) selectRef.current(id);
      });
      m.on("mouseenter", "segment-hit", () => (m.getCanvas().style.cursor = "pointer"));
      m.on("mouseleave", "segment-hit", () => (m.getCanvas().style.cursor = ""));

      m.on("click", (e) => {
        if (e.defaultPrevented) return;
        selectRef.current(null);
        tapRef.current([e.lngLat.lng, e.lngLat.lat]);
      });

      ready.current = true;
      m.fire("sw:ready");
    });

    return () => {
      m.remove();
      map.current = null;
      ready.current = false;
    };
  }, []);

  useEffect(() => {
    const m = map.current;
    if (!m) return;
    const apply = () => {
      (m.getSource("direct") as maplibregl.GeoJSONSource)?.setData(lineFC(routes?.shortest.polyline));
      (m.getSource("safer") as maplibregl.GeoJSONSource)?.setData(lineFC(routes?.safer.polyline));
      (m.getSource("segments") as maplibregl.GeoJSONSource)?.setData(segmentsFC(routes));
      if (routes) {
        const b = new maplibregl.LngLatBounds();
        toLngLat(routes.safer.polyline).forEach((c) => b.extend(c));
        toLngLat(routes.shortest.polyline).forEach((c) => b.extend(c));
        m.fitBounds(b, { padding: { top: 70, bottom: 260, left: 50, right: 50 }, duration: 700 });
      }
    };
    ready.current ? apply() : m.once("sw:ready", apply);
  }, [routes]);

  useEffect(() => {
    const m = map.current;
    if (!m) return;
    const apply = () => {
      const feats: GeoJSON.Feature[] = [];
      if (origin) feats.push({ type: "Feature", properties: { color: COLORS.origin }, geometry: { type: "Point", coordinates: origin } });
      if (dest) feats.push({ type: "Feature", properties: { color: COLORS.dest }, geometry: { type: "Point", coordinates: dest } });
      (m.getSource("pins") as maplibregl.GeoJSONSource)?.setData({ type: "FeatureCollection", features: feats });
    };
    ready.current ? apply() : m.once("sw:ready", apply);
  }, [origin, dest]);

  useEffect(() => {
    const m = map.current;
    if (!m) return;
    const apply = () =>
      m.setFilter("segment-selected", ["==", ["get", "segment_id"], selected ?? "__none__"]);
    ready.current ? apply() : m.once("sw:ready", apply);
  }, [selected]);

  return <div ref={container} className="map" />;
}
