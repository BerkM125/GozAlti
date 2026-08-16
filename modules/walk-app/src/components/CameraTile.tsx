import { useEffect, useRef, useState } from "react";
import Hls from "hls.js";
import { frameUrl } from "../api.ts";
import { FRESH_MAX_AGE_S } from "../config.ts";
import { familyOf, type Camera, type FrameRecord, type Observation } from "../types.ts";

/**
 * One camera. Live HLS where the camera has a stream *and* `live` is set, a
 * refreshed JPEG snapshot otherwise, and an explicit dead state when neither
 * works.
 *
 * `live` is opt-in because a playing HLS tile is a real viewer on SDOT's stream
 * host for as long as it is mounted, and media-ingest's HLS proxy has no gate
 * in front of it. Only the camera the user actually opened streams; the list
 * shows snapshots. See SPEC.md.
 *
 * Every tile carries an age badge. Passing an old frame off as current is the
 * one thing this component must never do, so the badge is not optional, it
 * never says LIVE over a still, and the snapshot refresh never runs faster than
 * media-ingest's 60 s per-camera floor.
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
  /** SPEC §6.1 record of the frame on screen. What the age badge reads. */
  record: FrameRecord | null;
  size?: "lg" | "md";
  /** Play the HLS stream. Off by default; only the opened camera sets it. */
  live?: boolean;
  /** Off when a surrounding sheet already names the camera. */
  caption?: boolean;
  onOpen?: () => void;
};

export default function CameraTile({
  camera,
  observation,
  record,
  size = "lg",
  live: wantLive = false,
  caption = true,
  onOpen,
}: Props) {
  const video = useRef<HTMLVideoElement>(null);
  const [feed, setFeed] = useState<Feed>("loading");
  const [bust, setBust] = useState(() => Date.now());
  const [now, setNow] = useState(() => Date.now());

  /** A stream exists and we are allowed to play it. */
  const streaming = wantLive && !!camera.live_hls;
  /**
   * Whether the <video> is still worth showing. Once the stream has fallen
   * back, keeping the element on screen leaves a dead black box where a
   * perfectly good snapshot could be.
   */
  const showVideo = streaming && feed !== "snap" && feed !== "dead";

  // Tick so the age badge stays honest without re-fetching anything.
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 5000);
    return () => clearInterval(t);
  }, []);

  // Live stream, where the camera has one and this tile is the opened one.
  useEffect(() => {
    if (!streaming) return;
    const node = video.current;
    if (!node) return;
    const src = camera.live_hls!;
    setFeed("loading");

    // hls.js first, native only as the fallback. The obvious order - ask the
    // element whether it can play HLS, and trust it - is wrong: Chromium
    // answers "maybe" for application/vnd.apple.mpegurl and then fails the
    // load with DEMUXER_ERROR_COULD_NOT_PARSE, because it cannot actually play
    // HLS. Media Source Extensions are the reliable signal, and where they
    // exist hls.js is the better player anyway. Safari has no MSE for HLS and
    // does play it natively, so it takes the second branch.
    if (!Hls.isSupported()) {
      if (node.canPlayType("application/vnd.apple.mpegurl")) {
        node.src = src;
        node.play().then(() => setFeed("live")).catch(() => setFeed("snap"));
      } else {
        setFeed("snap");
      }
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
  }, [streaming, camera.live_hls]);

  // Snapshot refresh, no faster than upstream allows. Runs whenever this tile
  // is showing stills - including for a stream-capable camera we chose not to
  // play, and for one whose stream died and fell back.
  useEffect(() => {
    if (streaming && feed === "live") return;
    const t = setInterval(() => setBust(Date.now()), SNAPSHOT_REFRESH_MS);
    return () => clearInterval(t);
  }, [streaming, feed]);

  const isLive = feed === "live" && streaming;
  const detections = observation?.detections ?? [];

  // The FrameRecord is the authority on how old the pixels are. An observation
  // only exists once a VLM has read the camera, so relying on it would leave
  // every tile without an age until then - which is how you end up with a bare
  // "SNAP" that says nothing, the exact thing invariant #2 forbids.
  const frame = record ?? observation?.frame ?? null;
  const age = ageLabel(frame?.captured_at, now);
  const stale = frame?.stale === true;

  // "STREAM" is a frame decoded out of the live stream, so it is seconds old,
  // not up to a minute. Naming the source keeps the number meaningful.
  const kind = frame?.source === "sdot-hls" ? "STREAM" : frame?.source === "disk-cache" ? "CACHED" : "SNAP";

  const badge = stale
    ? { text: "NO FRESH FRAME", cls: "is-dead" }
    : isLive
      ? { text: "LIVE", cls: "is-live" }
      : feed === "dead"
        ? { text: "NO FEED", cls: "is-dead" }
        : age
          ? { text: `${kind} ${age.text}`, cls: age.old ? "is-old" : "" }
          : // A frame with no known age must say so rather than imply currency.
            { text: "AGE UNKNOWN", cls: "is-old" };

  return (
    <figure className={`cam cam-${size}`}>
      <button
        className="cam-media"
        onClick={onOpen}
        aria-label={`Open ${camera.desc ?? camera.camera_id}`}
      >
        {showVideo ? (
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
        {/* This camera has a stream we are deliberately not playing here. Say
            it is available to open, never that it is playing. */}
        {camera.live_hls && !streaming && (
          <span className="cam-canlive" title="Tap to watch this camera live">
            ▶ LIVE
          </span>
        )}
        {/* On a route, how far along the walk matters more than how far off it:
            "600 m in" tells you when you pass it. Off-route distance still
            shows, because a camera 170 m away sees a different street. */}
        {typeof camera.along_m === "number" ? (
          <span className="cam-dist mono">
            {Math.round(camera.along_m)} m in
            {typeof camera.distance_m === "number" && camera.distance_m >= 25
              ? ` · ${Math.round(camera.distance_m)} m off`
              : ""}
          </span>
        ) : (
          typeof camera.distance_m === "number" && (
            <span className="cam-dist mono">{Math.round(camera.distance_m)} m</span>
          )
        )}
      </button>

      {caption && (
        <figcaption>
          {/* The intersection is what a walker recognises; the id is provenance. */}
          <span className="cam-where">{camera.desc ?? camera.street ?? "location unknown"}</span>
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
      )}
    </figure>
  );
}
