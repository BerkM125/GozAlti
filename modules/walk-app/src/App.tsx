import { useCallback, useEffect, useMemo, useState } from "react";
import MapView from "./components/MapView.tsx";
import { CameraPanel, NearbyPanel, SegmentSheet, type Placed } from "./components/Panels.tsx";
import { fetchCameras, fetchDetections, fetchRoute, health, RouteError } from "./api.ts";
import { NEARBY_CAMERA_LIMIT, NEARBY_RADIUS_M, WALK_M_PER_MIN } from "./config.ts";
import {
  isUnavailable,
  type Camera,
  type LngLat,
  type Observation,
  type RouteResult,
} from "./types.ts";

type View = "2D" | "3D";
type Panel = "cameras" | "nearby" | null;

const minutes = (m: number) => Math.max(1, Math.round(m / WALK_M_PER_MIN));

/** Metres between two lon/lat points. */
function dist(a: LngLat, b: LngLat): number {
  const R = 6_371_008.8;
  const p1 = (a[1] * Math.PI) / 180;
  const p2 = (b[1] * Math.PI) / 180;
  const dp = p2 - p1;
  const dl = ((b[0] - a[0]) * Math.PI) / 180;
  const h = Math.sin(dp / 2) ** 2 + Math.cos(p1) * Math.cos(p2) * Math.sin(dl / 2) ** 2;
  return 2 * R * Math.asin(Math.sqrt(h));
}

/** Index of the polyline vertex nearest `p`, used to order things along a route. */
function nearestIndex(polyline: [number, number][], p: LngLat): number {
  let best = 0;
  let bestD = Infinity;
  polyline.forEach(([lat, lon], i) => {
    const d = dist([lon, lat], p);
    if (d < bestD) {
      bestD = d;
      best = i;
    }
  });
  return best;
}

export default function App() {
  const [view, setView] = useState<View>("2D");

  // Origin and destination are one piece of state, not two. Two separate
  // setters read a stale closure when taps land in the same tick and can end up
  // routing a point to itself.
  const [pins, setPins] = useState<{ origin: LngLat | null; dest: LngLat | null }>({
    origin: null,
    dest: null,
  });
  const { origin, dest } = pins;
  const [userPos, setUserPos] = useState<LngLat | null>(null);

  const [result, setResult] = useState<RouteResult | null>(null);
  const [routing, setRouting] = useState(false);
  const [routeError, setRouteError] = useState<string | null>(null);

  const [cameras, setCameras] = useState<Camera[]>([]);
  const [cameraError, setCameraError] = useState<string | null>(null);
  const [observations, setObservations] = useState<Map<string, Observation>>(new Map());

  const [panel, setPanel] = useState<Panel>(null);
  const [selectedSegment, setSelectedSegment] = useState<string | null>(null);
  const [ingestUp, setIngestUp] = useState<boolean | null>(null);

  // -- is the camera service there at all? ---------------------------------
  useEffect(() => {
    health().then((h) => {
      if (!isUnavailable(h)) setIngestUp(h.media_ingest.ok);
    });
  }, []);

  // -- routing --------------------------------------------------------------
  useEffect(() => {
    if (!origin || !dest) return;
    let cancelled = false;
    setRouting(true);
    setRouteError(null);
    fetchRoute(origin, dest)
      .then((r) => !cancelled && setResult(r))
      .catch((e) => {
        if (cancelled) return;
        setResult(null);
        setRouteError(
          e instanceof RouteError ? e.message : "Couldn't reach the routing service.",
        );
      })
      .finally(() => !cancelled && setRouting(false));
    return () => {
      cancelled = true;
    };
  }, [origin, dest]);

  // -- cameras near the walker (or near the route while planning) ----------
  const focus = useMemo<LngLat | null>(() => {
    if (userPos) return userPos;
    if (result) {
      const mid = result.safer.polyline[Math.floor(result.safer.polyline.length / 2)];
      if (mid) return [mid[1], mid[0]];
    }
    return null;
  }, [userPos, result]);

  useEffect(() => {
    if (!focus) return;
    let cancelled = false;
    fetchCameras(focus[1], focus[0], NEARBY_RADIUS_M).then((res) => {
      if (cancelled) return;
      if (isUnavailable(res)) {
        setCameraError(res.why);
        setCameras([]);
        return;
      }
      setCameraError(null);
      const ranked = res.cameras
        .map((c) => ({ ...c, distance_m: dist(focus, [c.lon, c.lat]) }))
        // Cameras with a live stream earn their place first; distance breaks ties.
        .sort((a, b) => {
          const live = Number(!!b.live_hls) - Number(!!a.live_hls);
          return live !== 0 ? live : a.distance_m - b.distance_m;
        })
        .slice(0, NEARBY_CAMERA_LIMIT);
      setCameras(ranked);
    });
    return () => {
      cancelled = true;
    };
  }, [focus]);

  // -- VLM reads for exactly the cameras on screen -------------------------
  useEffect(() => {
    let cancelled = false;
    for (const c of cameras) {
      if (observations.has(c.camera_id)) continue;
      fetchDetections(c.camera_id).then((obs) => {
        if (cancelled || isUnavailable(obs)) return;
        setObservations((prev) => new Map(prev).set(c.camera_id, obs));
      });
    }
    return () => {
      cancelled = true;
    };
  }, [cameras]);

  // -- placing detections around the walker --------------------------------
  const { ahead, behind, unplaced, mapDetections } = useMemo(() => {
    const ahead: Placed[] = [];
    const behind: Placed[] = [];
    const unplaced: Placed[] = [];
    const mapDetections: { camera: Camera; detection: Placed["detection"] }[] = [];

    const line = result?.safer.polyline;
    const userIdx = line && userPos ? nearestIndex(line, userPos) : null;

    for (const camera of cameras) {
      const obs = observations.get(camera.camera_id);
      for (const detection of obs?.detections ?? []) {
        if (!detection.est) {
          unplaced.push({ camera, detection, alongM: null });
          continue;
        }
        mapDetections.push({ camera, detection });
        const at: LngLat = [detection.est.lon, detection.est.lat];
        if (line && userIdx !== null) {
          (nearestIndex(line, at) >= userIdx ? ahead : behind).push({
            camera,
            detection,
            alongM: detection.est.range_m,
          });
        } else {
          ahead.push({ camera, detection, alongM: detection.est.range_m });
        }
      }
    }
    return { ahead, behind, unplaced, mapDetections };
  }, [cameras, observations, result, userPos]);

  // -- interaction ----------------------------------------------------------
  // First tap sets the start, second the destination, third starts over.
  const onMapTap = useCallback((p: LngLat) => {
    setPins((prev) => (prev.origin && !prev.dest ? { ...prev, dest: p } : { origin: p, dest: null }));
  }, []);

  // Anything derived from a route is meaningless the moment the pins move.
  useEffect(() => {
    if (!origin || !dest) {
      setResult(null);
      setSelectedSegment(null);
      setRouteError(null);
    }
  }, [origin, dest]);

  const reset = () => {
    setPins({ origin: null, dest: null });
    setResult(null);
    setSelectedSegment(null);
    setRouteError(null);
  };

  const segment = result?.segments.find((s) => s.segment_id === selectedSegment) ?? null;
  const detourPct = result ? Math.round((result.detour_ratio - 1) * 100) : 0;

  return (
    <div className="app">
      <MapView
        view={view}
        result={result}
        origin={origin}
        dest={dest}
        cameras={cameras}
        detections={mapDetections}
        selectedSegment={selectedSegment}
        selectedCamera={null}
        onSelectSegment={setSelectedSegment}
        onSelectCamera={() => setPanel("cameras")}
        onMapTap={onMapTap}
        onUserLocation={setUserPos}
      />

      <header className="topbar glass">
        <span className="wordmark">GözAltı</span>
        {ingestUp === false && (
          <span className="chip chip-flag" title="Routing works; live camera evidence does not.">
            cameras offline
          </span>
        )}
        {(origin || result) && (
          <button className="text-btn" onClick={reset}>
            Reset
          </button>
        )}
      </header>

      <nav className="controls" aria-label="Map controls">
        <button
          className={`ctrl glass ${view === "3D" ? "is-on" : ""}`}
          onClick={() => setView(view === "3D" ? "2D" : "3D")}
          aria-pressed={view === "3D"}
          title="Tilt the map"
        >
          {view === "3D" ? "3D" : "2D"}
        </button>
        <button
          className={`ctrl glass ${panel === "cameras" ? "is-on" : ""}`}
          onClick={() => setPanel(panel === "cameras" ? null : "cameras")}
          aria-label="Cameras near you"
          title="Cameras near you"
        >
          <CameraIcon />
          {cameras.length > 0 && <span className="ctrl-badge mono">{cameras.length}</span>}
        </button>
        <button
          className={`ctrl glass ${panel === "nearby" ? "is-on" : ""}`}
          onClick={() => setPanel(panel === "nearby" ? null : "nearby")}
          aria-label="People and objects around you"
          title="Around you"
        >
          <PeopleIcon />
          {mapDetections.length > 0 && (
            <span className="ctrl-badge mono">{mapDetections.length}</span>
          )}
        </button>
      </nav>

      <div className={`dock glass ${result || routeError ? "" : "is-empty"}`}>
        {!origin && <p className="hint">Tap the map to set your start.</p>}
        {origin && !dest && <p className="hint">Now tap where you're walking to.</p>}
        {routing && <p className="hint">Finding a route…</p>}
        {routeError && <p className="banner banner-refuse">{routeError}</p>}

        {result && (
          <>
            {result.over_cap && (
              <p className="banner banner-flag">
                The lower-risk route is {detourPct}% longer, past the{" "}
                {Math.round((result.cap - 1) * 100)}% detour you allow. Both are shown.
              </p>
            )}
            <div className="stats">
              <div className="stat is-primary">
                <span className="stat-label">Recommended</span>
                <span className="stat-val mono">
                  {minutes(result.safer.length_m)} min · {(result.safer.length_m / 1000).toFixed(1)} km
                </span>
              </div>
              <div className="stat">
                <span className="stat-label">Direct</span>
                <span className="stat-val mono">
                  {minutes(result.shortest.length_m)} min ·{" "}
                  {(result.shortest.length_m / 1000).toFixed(1)} km
                </span>
              </div>
            </div>
            <p className="summary">{result.safer.evidence_summary}</p>
            {!segment && <p className="hint">Tap the route to see what shaped it.</p>}
          </>
        )}
      </div>

      {segment && <SegmentSheet segment={segment} onClose={() => setSelectedSegment(null)} />}

      {panel === "cameras" && (
        <CameraPanel
          cameras={cameras}
          observations={observations}
          unavailable={cameraError}
          onClose={() => setPanel(null)}
          onSelect={() => {}}
        />
      )}

      {panel === "nearby" && (
        <NearbyPanel
          ahead={ahead}
          behind={behind}
          unplaced={unplaced}
          hasRoute={!!result && !!userPos}
          onClose={() => setPanel(null)}
        />
      )}
    </div>
  );
}

const CameraIcon = () => (
  <svg viewBox="0 0 24 24" width="20" height="20" aria-hidden="true">
    <path
      fill="currentColor"
      d="M4 7h3l1.5-2h7L17 7h3a1 1 0 0 1 1 1v10a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V8a1 1 0 0 1 1-1Zm8 3.2A3.8 3.8 0 1 0 12 17.8 3.8 3.8 0 0 0 12 10.2Z"
    />
  </svg>
);

const PeopleIcon = () => (
  <svg viewBox="0 0 24 24" width="20" height="20" aria-hidden="true">
    <path
      fill="currentColor"
      d="M9 11a3.2 3.2 0 1 0 0-6.4A3.2 3.2 0 0 0 9 11Zm7.2.4a2.6 2.6 0 1 0 0-5.2 2.6 2.6 0 0 0 0 5.2ZM9 12.6c-2.9 0-6 1.5-6 3.6V19h12v-2.8c0-2.1-3.1-3.6-6-3.6Zm7.2.6c-.6 0-1.3.1-1.9.2 1 .8 1.7 1.9 1.7 3v3H22v-2.5c0-1.9-2.6-3.1-5.8-3.1Z"
    />
  </svg>
);
