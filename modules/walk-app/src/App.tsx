import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { FeatureCollection } from "geojson";
import MapView from "./components/MapView.tsx";
import { CameraIcon, ChevronUpIcon, LayersIcon, PeopleIcon } from "./components/icons.tsx";
import {
  CameraPanel,
  NearbyPanel,
  PathSegmentSheet,
  SegmentSheet,
  type Placed,
} from "./components/Panels.tsx";
import { CameraSheet } from "./components/CameraSheet.tsx";
import SearchBar, { type Field } from "./components/SearchBar.tsx";
import {
  fetchCameraCv,
  fetchCameras,
  fetchDetections,
  fetchFrameRecord,
  fetchLivePath,
  fetchPath,
  fetchRouterBlocks,
  fetchRoute,
  fetchRouteCameras,
  fetchSegment,
  health,
  RouteError,
  stopLivePath,
} from "./api.ts";
import { cvResultFeatures, placedCount, placedCounts, type CvFeature } from "./cvObjects.ts";
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
  type CvResult,
  type FrameRecord,
  type LngLat,
  type Observation,
  type PathPair,
  type RouteResult,
  type SegmentAssessment,
  type TripStop,
} from "./types.ts";

type View = "2D" | "3D";
type MapLayer = "weights" | "router" | "plain";
type Panel = "cameras" | "nearby" | null;

const minutes = (m: number) => Math.max(1, Math.round(m / WALK_M_PER_MIN));

const EMPTY_FC: FeatureCollection = { type: "FeatureCollection", features: [] };

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
  // The weight layer is the default view; the layers button cycles OSM
  // weights -> router evidence (when its artifacts exist) -> plain.
  const [layer, setLayer] = useState<MapLayer>("weights");

  // The consolidated router's static collision + osint evidence, fetched once.
  // Unavailable (artifacts not built) just leaves the cycle at two states -
  // the OSM weights layer is the always-working base.
  const [routerBlocks, setRouterBlocks] = useState<FeatureCollection | null>(null);
  useEffect(() => {
    fetchRouterBlocks().then((fc) => {
      if (!isUnavailable(fc)) setRouterBlocks(fc);
    });
  }, []);
  const cycleLayer = useCallback(() => {
    setLayer((l) => {
      if (l === "weights") return routerBlocks ? "router" : "plain";
      if (l === "router") return "plain";
      return "weights";
    });
  }, [routerBlocks]);

  // Origin and destination are one piece of state, not two. Two separate
  // setters read a stale closure when taps land in the same tick and can end up
  // routing a point to itself. Each stop carries the name the UI shows for it -
  // a searched place keeps its street name, a tap is honestly a "Dropped pin".
  const [trip, setTrip] = useState<{ origin: TripStop | null; dest: TripStop | null }>({
    origin: null,
    dest: null,
  });
  const origin = trip.origin?.point ?? null;
  const dest = trip.dest?.point ?? null;
  /** Which trip field the next map tap fills, when "Choose on the map" is active. */
  const [pickOnMap, setPickOnMap] = useState<Field | null>(null);
  const [userPos, setUserPos] = useState<LngLat | null>(null);
  /** Why we have no position, when we have none. Null once we do. */
  const [locationWhy, setLocationWhy] = useState<string | null>(null);

  /** Consolidated router (modules/pathfinding) result — the shipping one. */
  const [pathPair, setPathPair] = useState<PathPair | null>(null);
  /** Local in-process router result — only when media-ingest is unreachable. */
  const [result, setResult] = useState<RouteResult | null>(null);
  const [localFallback, setLocalFallback] = useState(false);
  const [routing, setRouting] = useState(false);
  const [routeError, setRouteError] = useState<string | null>(null);

  const [cameras, setCameras] = useState<Camera[]>([]);
  const [cameraError, setCameraError] = useState<string | null>(null);
  const [observations, setObservations] = useState<Map<string, Observation>>(new Map());
  const [records, setRecords] = useState<Map<string, FrameRecord>>(new Map());

  // -- local-CNN 3D objects (demo-ui row 7) --------------------------------
  // One store keyed by camera id, upsert-only: each camera's meshes are
  // replaced when its fresh result lands, and nothing is bulk-wiped while a
  // route is up, so already-rendered cars/people never blink out between
  // passes. Cleared only when the trip itself is cleared.
  const cvStore = useRef<Record<string, CvFeature[]>>({});
  const cvCountsRef = useRef<Record<string, { people: number; vehicles: number }>>({});
  const [cvObjects, setCvObjects] = useState<FeatureCollection>(EMPTY_FC);
  /** People/vehicles actually placed on the map, so the dock can say the
   *  objects exist without the user having to hunt for them at zoom. */
  const [cvTally, setCvTally] = useState({ people: 0, vehicles: 0, cameras: 0 });
  const ingestCv = useCallback((res: CvResult) => {
    if (!res?.ok || !res.camera_id) return;
    cvStore.current[res.camera_id] = cvResultFeatures(res);
    cvCountsRef.current[res.camera_id] = placedCounts(res);
    setCvObjects({
      type: "FeatureCollection",
      features: Object.values(cvStore.current).flat(),
    });
    const per = Object.values(cvCountsRef.current);
    setCvTally({
      people: per.reduce((n, c) => n + c.people, 0),
      vehicles: per.reduce((n, c) => n + c.vehicles, 0),
      cameras: per.filter((c) => c.people + c.vehicles > 0).length,
    });
  }, []);
  /** Bumping this cancels any in-flight en-route CV pass. */
  const cvPassToken = useRef(0);
  const clearCv = useCallback(() => {
    cvPassToken.current += 1;
    cvStore.current = {};
    cvCountsRef.current = {};
    setCvObjects(EMPTY_FC);
    setCvTally({ people: 0, vehicles: 0, cameras: 0 });
  }, []);

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
  // The consolidated router (real SDOT collisions, camera coverage, live
  // camera evidence in the weights) is tried first. The in-process router is
  // strictly a fallback for when media-ingest is not running, and says so.
  useEffect(() => {
    if (!origin || !dest) return;
    let cancelled = false;
    setRouting(true);
    setRouteError(null);
    (async () => {
      try {
        const pair = await fetchPath(origin, dest);
        if (cancelled) return;
        if (!isUnavailable(pair)) {
          setPathPair(pair);
          setResult(null);
          setLocalFallback(false);
          return;
        }
        setLocalFallback(true);
        const r = await fetchRoute(origin, dest);
        if (cancelled) return;
        setResult(r);
        setPathPair(null);
      } catch (e) {
        if (cancelled) return;
        setPathPair(null);
        setResult(null);
        setRouteError(
          e instanceof RouteError ? e.message : "Couldn't reach the routing service.",
        );
      } finally {
        if (!cancelled) setRouting(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [origin, dest]);

  // -- live session poll (demo-ui row 3, the live half) --------------------
  // The safer trip starts a PathLiveSession upstream; polling ~2 s brings
  // live camera evidence (occupancy, VLM flags) into the weights, and the
  // path AUTO-REPLACES when that evidence changes the optimum: a bumped
  // `version` carries a whole new PathObject. "expired" (404) means the
  // session is gone upstream and polling stops; a transient miss is ridden
  // out without tearing the route down.
  const liveVersion = useRef(0);
  const livePathId = pathPair?.safer.path_id ?? null;
  useEffect(() => {
    if (!livePathId || !pathPair) return;
    liveVersion.current = pathPair.safer.version;
    let stopped = false;
    const t = setInterval(async () => {
      const tick = await fetchLivePath(livePathId, liveVersion.current);
      if (stopped) return;
      if (tick === "expired") {
        clearInterval(t);
        return;
      }
      if (isUnavailable(tick)) return; // transient; next tick may answer
      if (tick.version > liveVersion.current) {
        liveVersion.current = tick.version;
        setPathPair((prev) =>
          prev && prev.safer.path_id === livePathId ? { ...prev, safer: tick.path } : prev,
        );
      }
    }, 2000);
    return () => {
      stopped = true;
      clearInterval(t);
      stopLivePath(livePathId);
    };
    // Keyed on the trip, not the PathObject: a version bump replaces the
    // object but must not restart the poll loop.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [livePathId]);

  // -- CV feed 1: cached results shipped WITH the path ---------------------
  // Renders instantly (age is honest via each result's frame_ts) and re-runs
  // on every live version bump, since the session refreshes corridor CV.
  useEffect(() => {
    if (!pathPair) return;
    Object.values(pathPair.safer.cv_detections ?? {}).forEach(ingestCv);
  }, [pathPair, ingestCv]);

  // -- CV feed 2: one HQ still pass per NEW trip ---------------------------
  // Exactly one detlib call per en-route camera, at most 4 in flight, run
  // once per path_id and not per version bump (the PathLiveSession keeps
  // corridor CV fresh on its own). detlib down => one plain retry, which
  // serves the yolo/cache lane instead of nothing.
  const stillPassPathId = pathPair?.safer.path_id ?? null;
  useEffect(() => {
    if (!stillPassPathId || !pathPair) return;
    const token = ++cvPassToken.current;
    const queue = [...pathPair.safer.cameras_en_route];
    if (queue.length === 0) return;
    const worker = async () => {
      while (queue.length > 0 && cvPassToken.current === token) {
        const cid = queue.shift()!;
        let res = await fetchCameraCv(cid, { backend: "detlib", force: true });
        if (cvPassToken.current !== token) return;
        if (isUnavailable(res) || !res.ok) {
          // detlib down is not "no CV": the local yolo lane still answers.
          const retry = await fetchCameraCv(cid, { backend: "yolo" });
          if (cvPassToken.current !== token || isUnavailable(retry)) continue;
          res = retry;
        }
        if (!isUnavailable(res)) ingestCv(res);
      }
    };
    for (let i = 0; i < Math.min(4, queue.length); i++) void worker();
    // Cancellation is the token bump in clearCv; no cleanup here because a
    // version bump must NOT abort a pass that is still working through the
    // same trip's cameras.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [stillPassPathId]);

  // -- CV feed 3: the opened camera, polled fast ---------------------------
  // The plain fast lane answers from media-ingest's cache in ms and never
  // triggers an upstream fetch, so 350 ms polling is rate-limit-proof; the
  // scene only rebuilds when the frame actually changed. The first time a
  // detection is actually placed, tilt to 3D once so the meshes read as 3D
  // (ref-guarded: the user's own toggle wins afterwards).
  const autoTilted = useRef(false);
  useEffect(() => {
    if (!selectedCamera) return;
    let active = true;
    let lastFrame: CvResult["frame_ts"] | undefined;
    const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));
    (async () => {
      while (active) {
        const res = await fetchCameraCv(selectedCamera);
        if (!active) return;
        if (isUnavailable(res) || !res.ok) {
          await sleep(2000); // service hiccup - back off a little extra
          continue;
        }
        if (res.frame_ts !== lastFrame) {
          lastFrame = res.frame_ts;
          ingestCv(res);
          if (placedCount(res) > 0 && !autoTilted.current) {
            autoTilted.current = true;
            setView((v) => (v === "2D" ? "3D" : v));
          }
        }
        await sleep(350);
      }
    })();
    return () => {
      active = false;
    };
  }, [selectedCamera, ingestCv]);

  // -- cameras: the whole route when there is one, else around you ---------
  // A route is a line, so asking for cameras "near" a single point on it would
  // only ever surface the handful by that point, however long the walk. With a
  // route active every camera overlooking it is shown, in passing order and
  // uncapped; without one the query falls back to a radius around you.
  // The consolidated router's polyline drives the same corridor query.
  const routeLine = pathPair?.safer.polyline ?? result?.safer.polyline ?? null;

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

  // Camera ids the consolidated router itself weights (coverage graph, not
  // the display corridor) — these get the en-route ring on the map.
  const routeCamIds = useMemo(
    () => new Set(pathPair?.safer.cameras_en_route ?? []),
    [pathPair],
  );

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

    const line = routeLine;
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
  }, [cameras, observations, routeLine, userPos]);

  // -- interaction ----------------------------------------------------------
  // Knowing where you are changes what a tap means. With a position, you are
  // the start and one tap is enough to route. Without one, first tap sets the
  // start, second the destination. With a full route on screen a tap re-anchors
  // the start and keeps the destination - the searched destination is the part
  // of the trip the user typed, so a stray tap must not throw it away. The
  // search bar's own "Choose on the map" overrides all of this for one tap.
  const onMapTap = useCallback(
    (p: LngLat) => {
      const pin: TripStop = { point: p, label: "Dropped pin" };
      if (pickOnMap) {
        setTrip((prev) => ({ ...prev, [pickOnMap]: pin }));
        setPickOnMap(null);
        return;
      }
      setTrip((prev) => {
        if (prev.origin && !prev.dest) return { ...prev, dest: pin };
        if (prev.origin && prev.dest) return { ...prev, origin: pin };
        if (userPos) {
          return { origin: { point: userPos, label: "Your location" }, dest: pin };
        }
        return { origin: pin, dest: null };
      });
    },
    [userPos, pickOnMap],
  );

  /**
   * A stop chosen through search. Picking a destination with no start yet
   * defaults the start to the walker, matching what one map tap does - but
   * only when a position actually exists; otherwise the planner shows an
   * explicit "Set your start" instead of guessing one.
   */
  const setStop = useCallback(
    (field: Field, stop: TripStop) => {
      setTrip((prev) => {
        const next = { ...prev, [field]: stop };
        if (field === "dest" && !next.origin && userPos) {
          next.origin = { point: userPos, label: "Your location" };
        }
        return next;
      });
    },
    [userPos],
  );

  const swapStops = useCallback(() => {
    setTrip((prev) => ({ origin: prev.dest, dest: prev.origin }));
  }, []);

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
      setPathPair(null);
      setResult(null);
      setSelectedSegment(null);
      setRouteError(null);
      clearCv();
    }
  }, [origin, dest, clearCv]);

  const reset = () => {
    setTrip({ origin: null, dest: null });
    setPickOnMap(null);
    setPathPair(null);
    setResult(null);
    setSelectedSegment(null);
    setRouteError(null);
    clearCv();
  };

  /** Opening a camera closes the list, so only one sheet is ever up. */
  const openCamera = useCallback((id: string) => {
    setSelectedCamera(id);
    setPanel(null);
  }, []);

  /**
   * The map's GeolocateControl is the other way a position arrives - and with
   * `trackUserLocation` it arrives continuously. Keeping the previous state
   * identity for sub-10 m jitter matters: every new `userPos` reference
   * cascades into a camera refetch and a marker pass, so GPS noise would
   * otherwise keep the whole camera layer gently churning.
   */
  const onGeolocate = useCallback((p: LngLat) => {
    setUserPos((prev) => (prev && dist(prev, p) < 10 ? prev : p));
    setLocationWhy(null);
  }, []);

  // -- tap-anywhere evidence ------------------------------------------------
  // A tapped block on the safer route already has its assessment in `result`;
  // any other block is fetched on demand from /api/segment/:id.
  const [fetchedSegment, setFetchedSegment] = useState<SegmentAssessment | null>(null);
  useEffect(() => {
    if (!selectedSegment) {
      setFetchedSegment(null);
      return;
    }
    if (result?.segments.some((s) => s.segment_id === selectedSegment)) return;
    let cancelled = false;
    fetchSegment(selectedSegment).then((s) => {
      if (cancelled || isUnavailable(s)) return;
      setFetchedSegment(s);
    });
    return () => {
      cancelled = true;
    };
  }, [selectedSegment, result]);

  const segment =
    result?.segments.find((s) => s.segment_id === selectedSegment) ??
    (fetchedSegment?.segment_id === selectedSegment ? fetchedSegment : null);
  const pfSegment =
    pathPair?.safer.segments.find((s) => s.segment_id === selectedSegment) ?? null;
  const camera = cameras.find((c) => c.camera_id === selectedCamera) ?? null;
  const detourPct = result ? Math.round((result.detour_ratio - 1) * 100) : 0;

  // Bucket counts for the consolidated route's legend, evidence attached via
  // the segment sheet — no number renders without a way to open its basis.
  const buckets = useMemo(() => {
    const b = { low: 0, medium: 0, high: 0 };
    for (const s of pathPair?.safer.segments ?? []) b[s.risk_bucket] += 1;
    return b;
  }, [pathPair]);

  return (
    <div className={`app ${trip.origin || trip.dest ? "has-trip" : ""}`}>
      <MapView
        view={view}
        layer={layer}
        routerBlocks={routerBlocks}
        pickingStop={pickOnMap !== null}
        result={result}
        path={pathPair}
        origin={origin}
        dest={dest}
        userPos={userPos}
        cameras={cameras}
        routeCamIds={routeCamIds}
        refuges={pathPair?.safer.refuges_en_route ?? []}
        cvObjects={cvObjects}
        detections={mapDetections}
        selectedSegment={selectedSegment}
        selectedCamera={selectedCamera}
        onSelectSegment={setSelectedSegment}
        onSelectCamera={openCamera}
        onMapTap={onMapTap}
        onUserLocation={onGeolocate}
      />

      <SearchBar
        origin={trip.origin}
        dest={trip.dest}
        userPos={userPos}
        pickOnMap={pickOnMap}
        onSetStop={setStop}
        onSwap={swapStops}
        onClear={reset}
        onPickOnMap={setPickOnMap}
      />

      <nav className="controls" aria-label="Map controls">
        <button
          className={`ctrl glass ${layer !== "plain" ? "is-on" : ""}`}
          onClick={cycleLayer}
          aria-pressed={layer !== "plain"}
          title={
            layer === "router"
              ? "Collision + report evidence (consolidated router)"
              : "Routing weight overlay"
          }
          aria-label="Routing weight overlay"
        >
          <LayersIcon />
        </button>
        <button
          className={`ctrl glass ${view === "3D" ? "is-on" : ""}`}
          onClick={() => setView(view === "3D" ? "2D" : "3D")}
          aria-pressed={view === "3D"}
          title="Tilt the map"
        >
          {view === "3D" ? "3D" : "2D"}
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

      {/* What the road colours mean. Named for what it is - a routing weight
          or recorded evidence, never a "safety" or "danger" score. */}
      {layer !== "plain" && (
        <div className="legend glass" aria-hidden="true">
          <span className="legend-title">
            {layer === "router" ? "collisions + reports" : "routing weight"}
          </span>
          <span className="legend-bar" />
          <span className="legend-ends">
            <i>lower</i>
            <i>higher</i>
          </span>
        </div>
      )}

      <div className={`dock glass ${result || pathPair || routeError || routing ? "" : "is-empty"}`}>
        {!origin && !dest && (
          <p className="hint">
            {userPos
              ? "Search a destination, or tap the map - the walk starts from you."
              : "Search a destination, or tap the map to set your start."}
          </p>
        )}
        {!origin && !dest && cameras.length > 0 && (
          <p className="hint">
            {cameras.length} camera{cameras.length === 1 ? "" : "s"} within{" "}
            {NEARBY_RADIUS_M} m of {userPos ? "you" : "downtown"} are on the map - tap one to
            watch its live view.
          </p>
        )}
        {origin && !dest && !pickOnMap && (
          <p className="hint">Now set where you're walking to.</p>
        )}
        {!origin && dest && !pickOnMap && (
          <p className="hint">Set your start to plan the walk.</p>
        )}
        {routing && <p className="hint">Finding a route…</p>}
        {routeError && <p className="banner banner-refuse">{routeError}</p>}

        {pathPair && !routing && (
          <>
            {pathPair.safer.detour_cap_hit && (
              <p className="banner banner-flag">
                Every lower-risk detour ran more than 25% longer than the direct walk, so the
                direct route is shown.
              </p>
            )}
            <div className="routes">
              <div className="route-card is-primary">
                <span className="route-tag">Recommended</span>
                <span className="route-time">
                  {Math.max(1, Math.round(pathPair.safer.eta_min))}
                  <small> min</small>
                </span>
                <span className="route-len mono">
                  {(pathPair.safer.length_m / 1000).toFixed(1)} km
                </span>
              </div>
              <div className="route-card">
                <span className="route-tag">Direct</span>
                <span className="route-time">
                  {Math.max(1, Math.round(pathPair.shortest.eta_min))}
                  <small> min</small>
                </span>
                <span className="route-len mono">
                  {(pathPair.shortest.length_m / 1000).toFixed(1)} km
                </span>
              </div>
            </div>
            <div className="pf-meta">
              <span className="pf-buckets" title={pathPair.safer.risk_basis}>
                <span className="pf-bucket">
                  <i className="pf-dot dot-low" />
                  {buckets.low}
                </span>
                <span className="pf-bucket">
                  <i className="pf-dot dot-medium" />
                  {buckets.medium}
                </span>
                <span className="pf-bucket">
                  <i className="pf-dot dot-high" />
                  {buckets.high}
                </span>
              </span>
              {pathPair.safer.live.incorporated ? (
                <span
                  className="chip chip-live"
                  title={`${pathPair.safer.live.basis}. Cameras reporting: ${
                    Object.keys(pathPair.safer.live.cameras_reporting ?? {}).length
                  }.`}
                >
                  live v{pathPair.safer.version}
                  {(pathPair.safer.live.layers_incorporated?.length ?? 0) > 0 &&
                    ` · ${pathPair.safer.live.layers_incorporated!.join("+")}`}
                </span>
              ) : (
                <span
                  className="chip chip-flag"
                  title={`This route is ${pathPair.safer.live.basis}. Pending layers: ${
                    pathPair.safer.live.layers_pending.join(", ") || "none"
                  }.`}
                >
                  live pending
                </span>
              )}
              <span className="pf-night">
                {pathPair.safer.night ? "night weights" : "daylight weights"}
              </span>
            </div>
            <p className="summary">{pathPair.safer.evidence_summary}</p>
            {pathPair.safer.refuges_en_route.length > 0 && (
              <p className="hint">
                {pathPair.safer.refuges_en_route.length} open business
                {pathPair.safer.refuges_en_route.length === 1 ? "" : "es"} along the way — the
                green dots.
              </p>
            )}
            {cvTally.people + cvTally.vehicles > 0 && (
              <p className="hint">
                Cameras see {cvTally.people}{" "}
                {cvTally.people === 1 ? "person" : "people"} · {cvTally.vehicles}{" "}
                {cvTally.vehicles === 1 ? "vehicle" : "vehicles"} on this route, drawn on the
                map ({cvTally.cameras} camera{cvTally.cameras === 1 ? "" : "s"}, local CV).
              </p>
            )}
            {!pfSegment && <p className="hint">Tap the route to see what shaped each block.</p>}
          </>
        )}

        {result && !routing && (
          <>
            {localFallback && (
              <p className="banner banner-flag">
                Consolidated router unreachable — this route came from the in-process fallback:
                OpenStreetMap tags only, no collision or camera evidence in the weights.
              </p>
            )}
            {result.over_cap && (
              <p className="banner banner-flag">
                The lower-risk route is {detourPct}% longer, past the{" "}
                {Math.round((result.cap - 1) * 100)}% detour you allow. Both are shown.
              </p>
            )}
            <div className="routes">
              <div className="route-card is-primary">
                <span className="route-tag">Recommended</span>
                <span className="route-time">
                  {minutes(result.safer.length_m)}
                  <small> min</small>
                </span>
                <span className="route-len mono">
                  {(result.safer.length_m / 1000).toFixed(1)} km
                </span>
              </div>
              <div className="route-card">
                <span className="route-tag">Direct</span>
                <span className="route-time">
                  {minutes(result.shortest.length_m)}
                  <small> min</small>
                </span>
                <span className="route-len mono">
                  {(result.shortest.length_m / 1000).toFixed(1)} km
                </span>
              </div>
            </div>
            <p className="summary">{result.safer.evidence_summary}</p>
            {!segment && <p className="hint">Tap the route to see what shaped it.</p>}
          </>
        )}
      </div>

      <button
        className="feeds-bar glass"
        onClick={() => setPanel(panel === "cameras" ? null : "cameras")}
        aria-expanded={panel === "cameras"}
        aria-label="Open the live camera feeds"
      >
        <CameraIcon size={18} />
        <span className="feeds-title">Live feeds</span>
        {ingestUp === false ? (
          <span className="chip chip-flag" title="Routing works; live camera evidence does not.">
            offline
          </span>
        ) : (
          cameras.length > 0 && <span className="feeds-count mono">{cameras.length}</span>
        )}
        <span className="feeds-chevron">
          <ChevronUpIcon />
        </span>
      </button>

      {segment && <SegmentSheet segment={segment} onClose={() => setSelectedSegment(null)} />}
      {pfSegment && pathPair && (
        <PathSegmentSheet
          segment={pfSegment}
          live={pathPair.safer.live}
          onClose={() => setSelectedSegment(null)}
        />
      )}

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
