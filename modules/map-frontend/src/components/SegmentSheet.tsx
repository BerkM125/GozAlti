import { COLORS } from "../config";
import { RISK_LABELS, type RiskParts, type SegmentDetail } from "../types";

function relTime(iso: string): string {
  const mins = Math.round((Date.now() - new Date(iso).getTime()) / 60000);
  if (!Number.isFinite(mins)) return "unknown";
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins} min ago`;
  return `${Math.round(mins / 60)} h ago`;
}

export default function SegmentSheet({ segment, onClose }: { segment: SegmentDetail; onClose: () => void }) {
  const parts = Object.entries(segment.risk_parts) as [keyof RiskParts, number][];
  const max = Math.max(...parts.map(([, v]) => v), 0.01);
  const worst = parts.reduce((a, b) => (b[1] > a[1] ? b : a))[0];

  return (
    <div className="sheet" role="dialog" aria-label={`Why ${segment.name} was avoided`}>
      <div className="sheet-grip" />
      <div className="sheet-head">
        <div>
          <p className="sheet-title">{segment.name}</p>
          <p className="sheet-sub">
            <span className="mono">{segment.segment_id}</span> · base risk{" "}
            <span className="mono">{segment.base_risk.toFixed(2)}</span>
          </p>
        </div>
        <button className="icon-btn" onClick={onClose} aria-label="Close">
          ✕
        </button>
      </div>

      <ul className="bars">
        {parts.map(([k, v]) => (
          <li key={k}>
            <span className="bar-label">{RISK_LABELS[k]}</span>
            <span className="bar-track">
              <span
                className="bar-fill"
                style={{
                  width: `${Math.round((v / max) * 100)}%`,
                  background: k === worst ? COLORS.flagged : COLORS.direct,
                }}
              />
            </span>
            <span className="bar-val mono">{v.toFixed(2)}</span>
          </li>
        ))}
      </ul>

      <div className="live-row">
        <span className="bar-label">Live</span>
        <span className={segment.stale ? "chip chip-stale" : "chip chip-live"}>
          {segment.stale
            ? "no fresh reading — neutral"
            : `${segment.live_penalty >= 0 ? "+" : ""}${segment.live_penalty.toFixed(2)} · conf ${segment.confidence.toFixed(2)}`}
        </span>
      </div>

      {segment.camera && (
        <div className="cam">
          <div className="cam-thumb" aria-hidden="true" />
          <div>
            <p className="cam-id mono">{segment.camera.id}</p>
            <p className="cam-meta">
              {relTime(segment.camera.ts)} · {segment.camera.surface} · occlusion {segment.camera.occlusion}
            </p>
          </div>
        </div>
      )}
    </div>
  );
}
