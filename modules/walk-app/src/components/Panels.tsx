import { useState, type ReactNode } from "react";
import CameraTile from "./CameraTile.tsx";
import { CloseIcon } from "./icons.tsx";
import {
  EVIDENCE_LABEL,
  FAMILY_LABEL,
  familyOf,
  type Camera,
  type Detection,
  type DetectionFamily,
  type FrameRecord,
  type Observation,
  type SegmentAssessment,
} from "../types.ts";

// ---------------------------------------------------------------------------
// Shared bottom sheet
// ---------------------------------------------------------------------------

export function Sheet({
  title,
  sub,
  onClose,
  children,
  tall,
}: {
  title: string;
  sub?: ReactNode;
  onClose: () => void;
  children: ReactNode;
  tall?: boolean;
}) {
  // Dismissal plays the slide-out first and unmounts on animationend. Under
  // prefers-reduced-motion the animations are disabled, animationend never
  // fires, and waiting for it would leave the sheet stuck - so close directly.
  const [closing, setClosing] = useState(false);
  const requestClose = () => {
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
      onClose();
      return;
    }
    setClosing(true);
  };
  const exit = closing ? "is-closing" : "";

  return (
    <>
      <div className={`scrim ${exit}`} onClick={requestClose} />
      <section
        className={`sheet ${tall ? "is-tall" : ""} ${exit}`}
        role="dialog"
        aria-label={title}
        onAnimationEnd={(e) => {
          // The entrance animation ends on this same element; only the
          // slide-out (and not a child's animation) may unmount the sheet.
          if (closing && e.target === e.currentTarget) onClose();
        }}
      >
        <div className="sheet-grip" />
        <header className="sheet-head">
          <div>
            <h2>{title}</h2>
            {sub && <p className="sheet-sub">{sub}</p>}
          </div>
          <button className="icon-btn" onClick={requestClose} aria-label="Close">
            <CloseIcon />
          </button>
        </header>
        <div className="sheet-body">{children}</div>
      </section>
    </>
  );
}

// ---------------------------------------------------------------------------
// Segment evidence
//
// This sheet is the product's core promise: the route weighted this block, and
// here is every input that did the weighting, each naming the OpenStreetMap tag
// it came from. There is no aggregate score on screen, because SPEC.md §6.4
// says `risk` is a routing weight and not something to show a user.
// ---------------------------------------------------------------------------

export function SegmentSheet({
  segment,
  onClose,
}: {
  segment: SegmentAssessment;
  onClose: () => void;
}) {
  const inferred = segment.evidence.filter((e) => e.ref === "osm:untagged").length;
  return (
    <Sheet
      title={segment.name}
      sub={
        <>
          <span className="mono">{segment.segment_id}</span> · {segment.length_m} m
        </>
      }
      onClose={onClose}
    >
      <p className="lede">Why the route weighted this block:</p>
      <ul className="evidence">
        {segment.evidence.map((e, i) => {
          const untagged = e.ref === "osm:untagged";
          return (
            <li key={i} className={untagged ? "is-inferred" : ""}>
              <span className="ev-type">{EVIDENCE_LABEL[e.type] ?? e.type}</span>
              <span className="ev-detail">{e.detail}</span>
              <span className="ev-ref mono">{untagged ? "not mapped" : e.ref}</span>
            </li>
          );
        })}
      </ul>
      {inferred > 0 && (
        <p className="note note-flag">
          {inferred} of {segment.evidence.length} inputs are not mapped in OpenStreetMap for this
          block. A neutral default was used, so this block is neither credited nor penalised for
          them.
        </p>
      )}
      <p className="note">
        Evidence comes from OpenStreetMap tags on this street. Nothing here is a safety verdict.
      </p>
    </Sheet>
  );
}

// ---------------------------------------------------------------------------
// Live camera feeds near you
// ---------------------------------------------------------------------------

export function CameraPanel({
  cameras,
  observations,
  records,
  onClose,
  onSelect,
  unavailable,
  locationWhy,
  onRoute,
}: {
  cameras: Camera[];
  observations: Map<string, Observation>;
  records: Map<string, FrameRecord>;
  onClose: () => void;
  onSelect: (id: string) => void;
  unavailable: string | null;
  /** Set when we have no position, so the title cannot claim "near you". */
  locationWhy: string | null;
  /** True when the list covers a planned route rather than a point. */
  onRoute: boolean;
}) {
  const title = onRoute
    ? "Cameras on your route"
    : locationWhy
      ? "Cameras downtown"
      : "Cameras near you";
  const sub = unavailable
    ? "camera service unavailable"
    : onRoute
      ? `${cameras.length} watching your way · in passing order · public SDOT feeds`
      : `${cameras.length} watching this area · nearest first · public SDOT feeds`;

  return (
    <Sheet title={title} sub={sub} onClose={onClose} tall>
      {/* Never let the list imply these are near the walker when they are not. */}
      {locationWhy && !unavailable && !onRoute && (
        <p className="note note-flag">
          {locationWhy} These are the cameras around downtown Seattle, not around you.
        </p>
      )}

      {unavailable ? (
        <p className="note note-refuse">
          {unavailable}. Routing still works; live camera evidence does not.
        </p>
      ) : cameras.length === 0 ? (
        <p className="note">
          {onRoute
            ? "No public cameras overlook this route. That is a gap in the camera network, not a judgement about the streets."
            : "No public cameras watch this stretch."}
        </p>
      ) : (
        <div className="cam-scroll">
          {cameras.map((c) => (
            <CameraTile
              key={c.camera_id}
              camera={c}
              observation={observations.get(c.camera_id) ?? null}
              record={records.get(c.camera_id) ?? null}
              onOpen={() => onSelect(c.camera_id)}
            />
          ))}
        </div>
      )}
    </Sheet>
  );
}

// ---------------------------------------------------------------------------
// Nearby people and objects
//
// "Ahead" and "behind" are measured along the route, not from a compass - the
// browser cannot get a reliable heading without a permission prompt this app
// does not need. With no route active the split is not knowable, so the panel
// says so instead of guessing.
// ---------------------------------------------------------------------------

export type Placed = { camera: Camera; detection: Detection; alongM: number | null };

export function NearbyPanel({
  ahead,
  behind,
  unplaced,
  hasRoute,
  onClose,
}: {
  ahead: Placed[];
  behind: Placed[];
  unplaced: Placed[];
  hasRoute: boolean;
  onClose: () => void;
}) {
  const total = ahead.length + behind.length + unplaced.length;
  return (
    <Sheet
      title="Around you"
      sub={`${total} object${total === 1 ? "" : "s"} read from nearby cameras`}
      onClose={onClose}
      tall
    >
      {total === 0 && (
        <p className="note">
          No objects returned by the camera reads yet. This means the cameras reported nothing, not
          that the street is empty.
        </p>
      )}

      {hasRoute ? (
        <>
          <DetectionGroup title="Ahead on your route" items={ahead} />
          <DetectionGroup title="Behind you" items={behind} />
        </>
      ) : (
        <DetectionGroup title="Nearby" items={[...ahead, ...behind]} />
      )}

      {unplaced.length > 0 && (
        <>
          <h3 className="group-title">Seen, but not placed</h3>
          <ul className="det-list">
            {unplaced.map((p, i) => (
              <li key={i} className="det-row is-unplaced">
                <span className={`swatch swatch-${familyOf(p.detection.label)}`} />
                <span className="det-label">{p.detection.label}</span>
                <span className="det-meta mono">{p.camera.camera_id}</span>
              </li>
            ))}
          </ul>
          <p className="note note-flag">
            These cameras have no resolved direction, so what they see cannot be put on the map. No
            position is estimated for them.
          </p>
        </>
      )}
    </Sheet>
  );
}

function DetectionGroup({ title, items }: { title: string; items: Placed[] }) {
  if (items.length === 0) return null;
  const byFamily = new Map<DetectionFamily, Placed[]>();
  for (const p of items) {
    const f = familyOf(p.detection.label);
    byFamily.set(f, [...(byFamily.get(f) ?? []), p]);
  }
  return (
    <>
      <h3 className="group-title">{title}</h3>
      <ul className="det-list">
        {[...byFamily.entries()].map(([family, group]) => (
          <li key={family} className="det-row">
            <span className={`swatch swatch-${family}`} />
            <span className="det-label">
              {group.length} {FAMILY_LABEL[family].toLowerCase()}
            </span>
            <span className="det-meta mono">
              {group
                .map((g) => g.detection.est?.range_m)
                .filter((r): r is number => typeof r === "number")
                .sort((a, b) => a - b)
                .slice(0, 1)
                .map((r) => `nearest ${Math.round(r)} m`)
                .join("") || "range unknown"}
            </span>
          </li>
        ))}
      </ul>
    </>
  );
}
