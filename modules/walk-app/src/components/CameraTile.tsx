import { useEffect, useRef, useState } from "react";
import Hls from "hls.js";
import { frameUrl } from "../api.ts";
import { FRESH_MAX_AGE_S } from "../config.ts";
import { familyOf, type Camera, type Observation } from "../types.ts";

/**
 * One camera. Live HLS where the camera has a stream, a refreshed JPEG snapshot
 * otherwise, and an explicit dead state when neither works.
 *
 * Every tile carries an age badge. Passing an old frame off as current is the
 * one thing this component must never do, so the badge is not optional and the
 * snapshot refresh never runs faster than media-ingest's 60 s per-camera floor.
 */

const SNAPSHOT_REFRESH_MS = 60_000; // matches SNAPSHOT_MIN_INTERVAL_S upstream

type Feed = "live" | "snap" | "dead" | "loading";

function ageLabel(iso: string | undefined, now: number): { text: string; old: boolean } | null {
  if (!iso) return null;
  const t = Date.parse(iso);
  if (!Number.isFinite(t)) return null;
  const s = Math.max(0, Math.round((now - t) / 1000));
  const old = s > FRESH_MAX_AGE_S;
  if (s < 60) return { text: `${s}s`, old };
  if (s < 3600) return { text: `${Math.round(s / 60)}m`, old };
  return { text: `${Math.round(s / 3600)}h`, old };
}

type Props = {
  camera: Camera;
  observation: Observation | null;
  size?: "lg" | "md";
  onOpen?: () => void;
};

export default function CameraTile({ camera, observation, size = "lg", onOpen }: Props) {
  const video = useRef<HTMLVideoElement>(null);
  const [feed, setFeed] = useState<Feed>("loading");
  const [bust, setBust] = useState(() => Date.now());
  const [now, setNow] = useState(() => Date.now());

  // Tick so the age badge stays honest without re-fetching anything.
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 5000);
    return () => clearInterval(t);
  }, []);

  // Live stream, where the camera has one.
  useEffect(() => {
    if (!camera.live_hls) return;
    const node = video.current;
    if (!node) return;
    const src = camera.live_hls;

    if (node.canPlayType("application/vnd.apple.mpegurl")) {
      node.src = src; // Safari and iOS play HLS natively
      node.play().then(() => setFeed("live")).catch(() => setFeed("snap"));
      return;
    }
    if (!Hls.isSupported()) {
      setFeed("snap");
      return;
    }
    const hls = new Hls({ liveDurationInfinity: true, enableWorker: true });
    hls.loadSource(src);
    hls.attachMedia(node);
    hls.on(Hls.Events.MANIFEST_PARSED, () => {
      node.play().then(() => setFeed("live")).catch(() => setFeed("snap"));
    });
    hls.on(Hls.Events.ERROR, (_e, data) => {
      if (data.fatal) {
        hls.destroy();
        setFeed("snap"); // fall back rather than showing a dead black box
      }
    });
    return () => hls.destroy();
  }, [camera.live_hls]);

  // Snapshot refresh, no faster than upstream allows.
  useEffect(() => {
    if (camera.live_hls) return;
    const t = setInterval(() => setBust(Date.now()), SNAPSHOT_REFRESH_MS);
    return () => clearInterval(t);
  }, [camera.live_hls]);

  const isLive = feed === "live" && !!camera.live_hls;
  const age = ageLabel(observation?.frame?.captured_at ?? observation?.analyzed_at, now);
  const stale = observation?.frame?.stale === true;
  const detections = observation?.detections ?? [];

  const badge = stale
    ? { text: "NO FRESH FRAME", cls: "is-dead" }
    : isLive
      ? { text: "LIVE", cls: "is-live" }
      : age
        ? { text: `SNAP ${age.text}`, cls: age.old ? "is-old" : "" }
        : feed === "dead"
          ? { text: "NO FEED", cls: "is-dead" }
          : { text: "SNAP", cls: "" };

  return (
    <figure className={`cam cam-${size}`}>
      <button className="cam-media" onClick={onOpen} aria-label={`Open ${camera.camera_id}`}>
        {camera.live_hls ? (
          <video ref={video} muted playsInline autoPlay preload="none" />
        ) : (
          <img
            src={`${frameUrl(camera.camera_id)}?t=${bust}`}
            alt={`Latest frame from camera ${camera.camera_id}`}
            loading="lazy"
            onLoad={() => setFeed("snap")}
            onError={() => setFeed("dead")}
          />
        )}

        {feed === "dead" && <span className="cam-dead">no frame available</span>}

        {/* Detections sit at their normalised cx/cy, top-left origin (SPEC §6.2). */}
        {detections.map((d, i) => (
          <span
            key={`${d.label}-${i}`}
            className={`det det-${familyOf(d.label)} ${d.conf < 0.5 ? "is-low" : ""}`}
            style={{ left: `${d.cx * 100}%`, top: `${d.cy * 100}%` }}
            title={`${d.label} · confidence ${d.conf.toFixed(2)}`}
          />
        ))}

        <span className={`badge ${badge.cls}`}>{badge.text}</span>
        {typeof camera.distance_m === "number" && (
          <span className="cam-dist mono">{Math.round(camera.distance_m)} m</span>
        )}
      </button>

      <figcaption>
        <span className="cam-id mono">{camera.camera_id}</span>
        {observation?.caption ? (
          <span className="cam-caption">{observation.caption}</span>
        ) : (
          <span className="cam-caption is-muted">
            {observation === null ? "no camera read yet" : "no description returned"}
          </span>
        )}
        {detections.length > 0 && (
          <span className="cam-count mono">
            {detections.filter((d) => familyOf(d.label) === "person").length} people ·{" "}
            {detections.length} objects
          </span>
        )}
      </figcaption>
    </figure>
  );
}
