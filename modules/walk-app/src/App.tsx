import { useCallback, useEffect, useMemo, useState } from "react";
import MapView from "./components/MapView.tsx";
import { CameraPanel, NearbyPanel, SegmentSheet, type Placed } from "./components/Panels.tsx";
import { CameraSheet } from "./components/CameraSheet.tsx";
import {
  fetchCameras,
  fetchDetections,
  fetchFrameRecord,
  fetchRoute,
  fetchRouteCameras,
  health,
  RouteError,
} from "./api.ts";
import {
  CENTER,
  NEARBY_CAMERA_LIMIT,
  NEARBY_RADIUS_M,
  ROUTE_CORRIDOR_M,
  WALK_M_PER_MIN,
} from "./config.ts";
import {
  isObservation,
  isUnavailable,
  type Camera,
  type FrameRecord,
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
  /** Why we have no position, when we have none. Null once we do. */
  const [locationWhy, setLocationWhy] = useState<string | null>(null);

  const [result, setResult] = useState<RouteResult | null>(null);
  const [routing, setRouting] = useState(false);
  const [routeError, setRouteError] = useState<string | null>(null);

  const [cameras, setCameras] = useState<Camera[]>([]);
  const [cameraError, setCameraError] = useState<string | null>(null);
  const [observations, setObservations] = useState<Map<string, Observation>>(new Map());
  const [records, setRecords] = useState<Map<string, FrameRecord>>(new Map());

  const [panel, setPanel] = useState<Panel>(null);
  const [selectedSegment, setSelectedSegment] = useState<string | null>(null);
  const [selectedCamera, setSelectedCamera] = useState<string | null>(null);
  const [ingestUp, setIngestUp] = useState<boolean | null>(null);

  // -- is the camera service there at all? ---------------------------------
  useEffect(() => {
    health().then((h) => {
      if (!isUnavailable(h)) setIngestUp(h.media_ingest.ok);
    });
  }, []);

  // -- where is the walker? -------------------------------------------------
  // Asked for once on load, so cameras appear without hunting for a control.
  // The GeolocateControl on the map stays, for re-centring and the blue dot.
  //
  // Failure is normal and must be stated, never papered over: the browser only
  // exposes a position in a secure context, so a phone opening the dev server
  // over a plain-HTTP LAN address gets nothing. Falling back to downtown keeps
  // the app useful, but the UI has to say the cameras are not near *you*.
  useEffect(() => {
    if (!("geolocation" in navigator)) {
      setLocationWhy("This browser cannot share a location.");
      return;
    }
    if (!window.isSecureContext) {
      setLocationWhy(
        "Location needs a secure connection. Open the app over HTTPS (bun run tunnel) or on localhost.",
      );
      return;
    }
    let cancelled = false;
    navigator.geolocation.getCurrentPosition(
      (pos) => {
        if (cancelled) return;
        setUserPos([pos.coords.longitude, pos.coords.latitude]);
        setLocationWhy(null);
      },
      (err) => {
        if (cancelled) return;
        setLocationWhy(
          err.code === err.PERMISSION_DENIED
            ? "Location permission was declined."
            : "Your location could not be determined.",
        );
      },
      { enableHighAccuracy: true, timeout: 8000 },
    );
    return () => {
      cancelled = true;
    };
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

  // -- cameras: the whole route when there is one, else around you ---------
  // A route is a line, so asking for cameras "near" a single point on it would
  // only ever surface the handful by that point, however long the walk. With a
  // route active every camera overlooking it is shown, in passing order and
  // uncapped; without one the query falls back to a radius around you.
  const routeLine = result?.safer.polyline ?? null;

  const focus = useMemo<LngLat>(() => {
    if (userPos) return userPos;
    return CENTER;
  }, [userPos]);

  useEffect(() => {
    let cancelled = false;

    const load = routeLine
      ? fetchRouteCameras(routeLine, ROUTE_CORRIDOR_M)
      : fetchCameras(focus[1], focus[0], NEARBY_RADIUS_M);

    load.then((res) => {
      if (cancelled) return;
      if (isUnavailable(res)) {
        setCameraError(res.why);
        setCameras([]);
        return;
      }
      setCameraError(null);

      if (routeLine) {
        // Already ordered along the route by the server, and deliberately not
        // truncated: a camera near the far end matters just as much as one by
        // the start.
        setCameras(res.cameras);
        return;
      }

      // No route: nearest to you wins, full stop. Only the opened camera
      // streams, so having a stream is no reason to bump a closer camera down.
      setCameras(
        res.cameras
          .map((c) => ({ ...c, distance_m: c.distance_m ?? dist(focus, [c.lon, c.lat]) }))
          .sort((a, b) => (a.distance_m ?? 0) - (b.distance_m ?? 0))
          .slice(0, NEARBY_CAMERA_LIMIT),
      );
    });
    return () => {
      cancelled = true;
    };
  }, [focus, routeLine]);

  // -- how old is each frame on screen? ------------------------------------
  // Invariant #2: every camera image carries an honest age, and that has to
  // hold with no VLM in the picture. `/api/frame/:cid/record` answers from
  // media-ingest's memory and never triggers an upstream fetch, so polling it
  // on the tile refresh cadence costs nothing. It 404s until that camera's
  // first frame lands, hence the early retry.
  useEffect(() => {
    if (cameras.length === 0) return;
    let cancelled = false;

    // Which cameras have answered at least once, tracked here rather than read
    // back out of state, so the retry does not depend on a render having
    // happened yet.
    const got = new Set<string>();

    /** `onlyMissing` limits the sweep to cameras that have never answered. */
    const pull = (onlyMissing = false) => {
      for (const c of cameras) {
        if (onlyMissing && got.has(c.camera_id)) continue;
        fetchFrameRecord(c.camera_id).then((r) => {
          if (cancelled || isUnavailable(r) || typeof r?.captured_at !== "string") return;
          got.add(c.camera_id);
          setRecords((prev) => new Map(prev).set(c.camera_id, r));
        });
      }
    };

    pull();
    // A record 404s until that camera's first frame lands, so retry - but only
    // for the ones still missing. A route can carry thirty cameras, and
    // re-asking for all of them would be a burst of pointless requests.
    const retry = setTimeout(() => pull(true), 4000);
    // 60 s, matching the snapshot floor: a record cannot meaningfully change
    // faster than the frame the tile is showing.
    const t = setInterval(() => pull(false), 60_000);
    return () => {
      cancelled = true;
      clearTimeout(retry);
      clearInterval(t);
    };
  }, [cameras]);

  // -- VLM reads for exactly the cameras on screen -------------------------
  useEffect(() => {
    let cancelled = false;
    for (const c of cameras) {
      if (observations.has(c.camera_id)) continue;
      fetchDetections(c.camera_id).then((obs) => {
        // `{}` comes back for a camera nothing has analysed yet. Storing it
        // would make the tile claim the camera returned no description, when
        // in truth it was never read.
        if (cancelled || !isObservation(obs)) return;
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
  // Knowing where you are changes what a tap means. With a position, you are
  // the start and one tap is enough to route. Without one, it stays the old
  // two-tap flow: first tap the start, second the destination, third start over.
  const onMapTap = useCallback(
    (p: LngLat) => {
      setPins((prev) => {
        if (prev.origin && !prev.dest) return { ...prev, dest: p };
        if (prev.origin) return { origin: p, dest: null }; // third tap resets
        if (userPos) return { origin: userPos, dest: p };
        return { origin: p, dest: null };
      });
    },
    [userPos],
  );

  // `cameras` is refetched whenever `focus` moves. A selection that dropped
  // out of the list would leave the viewer showing nothing.
  useEffect(() => {
    if (selectedCamera && !cameras.some((c) => c.camera_id === selectedCamera)) {
      setSelectedCamera(null);
    }
  }, [cameras, selectedCamera]);

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

  /** Opening a camera closes the list, so only one sheet is ever up. */
  const openCamera = useCallback((id: string) => {
    setSelectedCamera(id);
    setPanel(null);
  }, []);

  /** The map's GeolocateControl is the other way a position arrives. */
  const onGeolocate = useCallback((p: LngLat) => {
    setUserPos(p);
    setLocationWhy(null);
  }, []);

  const segment = result?.segments.find((s) => s.segment_id === selectedSegment) ?? null;
  const camera = cameras.find((c) => c.camera_id === selectedCamera) ?? null;
  const detourPct = result ? Math.round((result.detour_ratio - 1) * 100) : 0;

  return (
    <div className="app">
      <MapView
        view={view}
        result={result}
        origin={origin}
        dest={dest}
        userPos={userPos}
        cameras={cameras}
        detections={mapDetections}
        selectedSegment={selectedSegment}
        selectedCamera={selectedCamera}
        onSelectSegment={setSelectedSegment}
        onSelectCamera={openCamera}
        onMapTap={onMapTap}
        onUserLocation={onGeolocate}
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
        {!origin &&
          (userPos ? (
            <p className="hint">Tap where you're walking to. The route starts from you.</p>
          ) : (
            <p className="hint">Tap the map to set your start.</p>
          ))}
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

      {camera && (
        <CameraSheet
          camera={camera}
          observation={observations.get(camera.camera_id) ?? null}
          record={records.get(camera.camera_id) ?? null}
          onClose={() => setSelectedCamera(null)}
        />
      )}

      {panel === "cameras" && !camera && (
        <CameraPanel
          cameras={cameras}
          observations={observations}
          records={records}
          unavailable={cameraError}
          locationWhy={locationWhy}
          onRoute={!!routeLine}
          onClose={() => setPanel(null)}
          onSelect={openCamera}
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
