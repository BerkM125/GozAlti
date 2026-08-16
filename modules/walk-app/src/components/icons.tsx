/**
 * The app's whole icon set, SF-Symbols style: 24-unit grid, 1.8-unit rounded
 * stroke, drawn with `currentColor` so each icon takes the text colour of
 * whatever it sits in.
 *
 * Two weights on purpose, mirroring how SF Symbols vary weight with size:
 * stroke outlines for 20px chrome, and a filled glyph for the 13px map marker,
 * where a 1.8-unit stroke would render sub-pixel and shimmer.
 */

type IconProps = { size?: number };

const stroke = {
  fill: "none",
  stroke: "currentColor",
  strokeWidth: 1.8,
  strokeLinecap: "round",
  strokeLinejoin: "round",
} as const;

export const CameraIcon = ({ size = 20 }: IconProps) => (
  <svg viewBox="0 0 24 24" width={size} height={size} aria-hidden="true" {...stroke}>
    <rect x="3" y="7.2" width="18" height="11.8" rx="2.5" />
    <path d="M8.3 7.2 9.9 5h4.2l1.6 2.2" />
    <circle cx="12" cy="13" r="3.4" />
  </svg>
);

export const PeopleIcon = ({ size = 20 }: IconProps) => (
  <svg viewBox="0 0 24 24" width={size} height={size} aria-hidden="true" {...stroke}>
    <circle cx="9" cy="8" r="3.1" />
    <path d="M3.5 19v-1.3c0-2.3 2.6-3.7 5.5-3.7s5.5 1.4 5.5 3.7V19" />
    <circle cx="16.8" cy="9" r="2.5" />
    <path d="M16.5 14.2c2.4.2 4.3 1.4 4.3 3.3V19" />
  </svg>
);

export const CloseIcon = ({ size = 14 }: IconProps) => (
  <svg
    viewBox="0 0 24 24"
    width={size}
    height={size}
    aria-hidden="true"
    {...stroke}
    strokeWidth={2}
  >
    <path d="M6.5 6.5l11 11M17.5 6.5l-11 11" />
  </svg>
);

export const PlayIcon = ({ size = 10 }: IconProps) => (
  <svg viewBox="0 0 24 24" width={size} height={size} aria-hidden="true">
    <path
      fill="currentColor"
      d="M8.6 5.9v12.2c0 .9 1 1.5 1.8 1l9-6.1a1.2 1.2 0 0 0 0-2l-9-6.1c-.8-.5-1.8.1-1.8 1Z"
    />
  </svg>
);

/**
 * The filled camera for MapLibre markers, as markup: markers are plain DOM
 * nodes built with innerHTML, not React (see MapView's `el`).
 */
export const CAMERA_MARKER_GLYPH =
  '<svg viewBox="0 0 24 24" width="13" height="13" aria-hidden="true">' +
  '<path fill="currentColor" d="M4 7h3l1.5-2h7L17 7h3a1 1 0 0 1 1 1v10a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V8a1 1 0 0 1 1-1Zm8 3.2A3.8 3.8 0 1 0 12 17.8 3.8 3.8 0 0 0 12 10.2Z"/>' +
  "</svg>";
