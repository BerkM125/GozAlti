import { Sheet } from "./Panels.tsx";
import CameraTile from "./CameraTile.tsx";
import { familyOf, type Camera, type FrameRecord, type Observation } from "../types.ts";

/**
 * One camera, full-bleed, and the only place in the app that plays live video.
 *
 * Every playing HLS tile is a real viewer on SDOT's stream host for as long as
 * it is mounted, and media-ingest's HLS proxy is a straight passthrough with no
 * gate. Confining playback to the single camera the user opened bounds that to
 * one viewer. Closing the sheet unmounts the tile, which destroys the hls.js
 * instance and stops the segment requests.
 */

export function CameraSheet({
  camera,
  observation,
  record,
  onClose,
}: {
  camera: Camera;
  observation: Observation | null;
  record: FrameRecord | null;
  onClose: () => void;
}) {
  const detections = observation?.detections ?? [];
  const people = detections.filter((d) => familyOf(d.label) === "person").length;

  return (
    <Sheet
      title={camera.desc ?? camera.street ?? camera.camera_id}
      sub={
        <>
          <span className="mono">{camera.camera_id}</span>
          {typeof camera.distance_m === "number" && <> · {Math.round(camera.distance_m)} m away</>}
          {/* Capability, not currency. The badge on the frame says how old the
              pixels actually are; this must not compete with it. */}
          {camera.live_hls ? " · live stream available" : " · snapshots only"}
        </>
      }
      onClose={onClose}
    >
      <div className="cam-full">
        {/* `key` forces a fresh tile per camera, so switching cameras tears the
            previous hls.js instance down instead of reusing its media element. */}
        <CameraTile
          key={camera.camera_id}
          camera={camera}
          observation={observation}
          record={record}
          live
          caption={false}
        />
      </div>

      {observation?.caption && <p className="lede">{observation.caption}</p>}

      {detections.length > 0 ? (
        <p className="note">
          {people} {people === 1 ? "person" : "people"} and {detections.length - people} other{" "}
          {detections.length - people === 1 ? "object" : "objects"} in this frame. Dots sit where
          the camera read them.
        </p>
      ) : (
        <p className="note">
          {observation === null
            ? "This camera has not been read yet, so nothing is marked on the frame. That is not the same as the street being empty."
            : "The camera read returned no objects."}
        </p>
      )}

      {/* Invariant #3/#4: an unresolved bearing means nothing this camera sees
          can be placed, and we say so rather than leaving the map silent. */}
      {(camera.bearing_deg === null || camera.bearing_conf === null) && (
        <p className="note note-flag">
          This camera's direction is unresolved, so what it sees cannot be placed on the map. No
          position is estimated for it.
        </p>
      )}

      <p className="note">
        Public SDOT feed, served through media-ingest. Snapshots refresh no faster than once a
        minute.
      </p>
    </Sheet>
  );
}
