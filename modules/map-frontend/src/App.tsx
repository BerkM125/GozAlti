import { useEffect, useMemo, useState } from "react";
import MapView from "./components/MapView";
import SegmentSheet from "./components/SegmentSheet";
import { getRoutes, RouteRefused } from "./api";
import { DETOUR_CAP, USE_MOCK, WALK_M_PER_MIN } from "./config";
import type { LngLat, Refusal, RoutePair } from "./types";

const minutesFor = (lengthM: number) => Math.max(1, Math.round(lengthM / WALK_M_PER_MIN));

export default function App() {
  const [origin, setOrigin] = useState<LngLat | null>(null);
  const [dest, setDest] = useState<LngLat | null>(null);
  const [routes, setRoutes] = useState<RoutePair | null>(null);
  const [refusal, setRefusal] = useState<Refusal | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [selected, setSelected] = useState<string | null>(null);

  function onMapTap(p: LngLat) {
    if (!origin || (origin && dest)) {
      setOrigin(p);
      setDest(null);
      setRoutes(null);
      setRefusal(null);
      setError(null);
    } else {
      setDest(p);
    }
  }

  useEffect(() => {
    if (!origin || !dest) return;
    let cancelled = false;
    setLoading(true);
    setError(null);
    setRefusal(null);
    getRoutes(origin, dest)
      .then((r) => !cancelled && setRoutes(r))
      .catch((e) => {
        if (cancelled) return;
        if (e instanceof RouteRefused) setRefusal(e.detail);
        else setError("Couldn't reach the routing service. Check the connection and try again.");
      })
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
  }, [origin, dest]);

  const segment = useMemo(
    () => routes?.safer.segments?.find((s) => s.segment_id === selected) ?? null,
    [routes, selected],
  );

  const detourRatio = useMemo(
    () => (routes && routes.shortest.length_m ? routes.safer.length_m / routes.shortest.length_m : 1),
    [routes],
  );
  const overCap = routes ? detourRatio > DETOUR_CAP : false;

  function reset() {
    setOrigin(null);
    setDest(null);
    setRoutes(null);
    setSelected(null);
    setRefusal(null);
    setError(null);
  }

  return (
    <div className="app">
      <header className="topbar">
        <span className="wordmark">safe walk</span>
        {USE_MOCK && <span className="chip chip-mock mono">mock</span>}
        <button className="text-btn" onClick={reset}>
          Reset
        </button>
      </header>

      <MapView
        routes={routes}
        origin={origin}
        dest={dest}
        selected={selected}
        onSelect={setSelected}
        onMapTap={onMapTap}
      />

      <div className="dock">
        {!origin && <p className="hint">Tap the map to set your start.</p>}
        {origin && !dest && <p className="hint">Now tap your destination.</p>}
        {loading && <p className="hint">Routing…</p>}

        {error && <p className="banner banner-error">{error}</p>}

        {refusal && (
          <p className="banner banner-refusal">
            No safer route within {refusal.cap}× the direct distance. Avoiding this area would mean a{" "}
            {refusal.detour_ratio.toFixed(2)}× detour, so Safe Walk returns the direct route instead.
          </p>
        )}

        {routes && !refusal && (
          <>
            {overCap && (
              <p className="banner banner-refusal">
                Detour {detourRatio.toFixed(2)}× exceeds the {DETOUR_CAP}× cap.
              </p>
            )}
            <div className="stats">
              <div className="stat">
                <span className="stat-label">Safer</span>
                <span className="stat-val">
                  {minutesFor(routes.safer.length_m)} min · {(routes.safer.length_m / 1000).toFixed(1)} km
                </span>
              </div>
              <div className="stat">
                <span className="stat-label">Direct</span>
                <span className="stat-val">
                  {minutesFor(routes.shortest.length_m)} min · {(routes.shortest.length_m / 1000).toFixed(1)} km
                </span>
              </div>
              <div className="stat">
                <span className="stat-label">Detour</span>
                <span className="stat-val">
                  {detourRatio.toFixed(2)}×{" "}
                  <span className={overCap ? "cap cap-over" : "cap cap-ok"}>
                    {overCap ? "over cap" : "under cap"}
                  </span>
                </span>
              </div>
            </div>
            {!segment && <p className="hint">Tap a segment of the green route to see why.</p>}
          </>
        )}
      </div>

      {segment && <SegmentSheet segment={segment} onClose={() => setSelected(null)} />}
    </div>
  );
}
