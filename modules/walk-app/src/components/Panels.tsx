import { useState, type ReactNode } from "react";
import CameraTile from "./CameraTile.tsx";
import { CloseIcon } from "./icons.tsx";
import { riskToT, weightColor } from "../palette.ts";
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
// Block assessment
//
// The product's core promise, made visual: where this block sits on the
// routing-weight scale, and the four inputs that put it there. The scale is
// labeled for what it is - a routing weight - and the dots reuse the map's
// ramp, so the sheet and the streets speak one colour language. A hollow dot
// plus a "not mapped" chip keeps the honesty about inputs OSM does not record.
// ---------------------------------------------------------------------------

export function SegmentSheet({
  segment,
  onClose,
}: {
  segment: SegmentAssessment;
  onClose: () => void;
}) {
  return (
    <Sheet title={segment.name} sub={`${segment.length_m} m`} onClose={onClose}>
      <div className="scale">
        <span className="scale-cap">Routing weight</span>
        <div className="scale-bar">
          <span className="scale-dot" style={{ left: `${riskToT(segment.risk) * 100}%` }} />
        </div>
        <span className="scale-ends">
          <i>lower</i>
          <i>higher</i>
        </span>
      </div>

      <ul className="inputs">
        {segment.evidence.map((e, i) => {
          const untagged = e.ref === "osm:untagged";
          return (
            <li key={i}>
              {untagged ? (
                <span className="in-dot is-inferred" />
              ) : (
                <span className="in-dot" style={{ background: weightColor(e.score ?? 0) }} />
              )}
              <span className="in-type">{EVIDENCE_LABEL[e.type] ?? e.type}</span>
              <span className="in-detail">{e.detail}</span>
              {untagged && <span className="in-chip">not mapped</span>}
            </li>
          );
        })}
      </ul>

      <p className="note">From OpenStreetMap tags on this street - not a safety verdict.</p>
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
        // A vertical feed, one full-width card per camera, scrolled through the
        // sheet body. Tiles lazy-load their frames, so an uncapped route list
        // still only fetches the frames actually scrolled into view.
        <div className="cam-feed">
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
