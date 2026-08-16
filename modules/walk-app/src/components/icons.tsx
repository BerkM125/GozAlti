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

export const SearchIcon = ({ size = 18 }: IconProps) => (
  <svg viewBox="0 0 24 24" width={size} height={size} aria-hidden="true" {...stroke}>
    <circle cx="10.8" cy="10.8" r="6.3" />
    <path d="m15.6 15.6 4.9 4.9" />
  </svg>
);

/** Two opposed arrows: swap the start and the destination. */
export const SwapIcon = ({ size = 18 }: IconProps) => (
  <svg viewBox="0 0 24 24" width={size} height={size} aria-hidden="true" {...stroke}>
    <path d="M8 4.5v13.5M8 4.5 4.6 7.9M8 4.5l3.4 3.4" />
    <path d="M16 19.5V6M16 19.5l3.4-3.4M16 19.5l-3.4-3.4" />
  </svg>
);

/** A map pin: destinations, and results that are a single point. */
export const PinIcon = ({ size = 18 }: IconProps) => (
  <svg viewBox="0 0 24 24" width={size} height={size} aria-hidden="true" {...stroke}>
    <path d="M12 21s-6.4-5.5-6.4-10.4a6.4 6.4 0 1 1 12.8 0C18.4 15.5 12 21 12 21Z" />
    <circle cx="12" cy="10.4" r="2.3" />
  </svg>
);

/** Crossed streets: an intersection search result. */
export const CrossroadIcon = ({ size = 18 }: IconProps) => (
  <svg viewBox="0 0 24 24" width={size} height={size} aria-hidden="true" {...stroke}>
    <path d="M12 3v18M3 12h18" />
    <path d="M8.5 3v5.5H3M15.5 21v-5.5H21" opacity="0.45" />
  </svg>
);

/** A road vanishing to a point: a street search result. */
export const RoadIcon = ({ size = 18 }: IconProps) => (
  <svg viewBox="0 0 24 24" width={size} height={size} aria-hidden="true" {...stroke}>
    <path d="M5 20 9.6 4M19 20 14.4 4" />
    <path d="M12 5.5v2.4M12 11v2.4M12 16.5v2.4" />
  </svg>
);

/** Navigation arrow: "your location". */
export const LocateIcon = ({ size = 18 }: IconProps) => (
  <svg viewBox="0 0 24 24" width={size} height={size} aria-hidden="true" {...stroke}>
    <path d="M20.5 3.5 4 10.4l7 2.6 2.6 7 6.9-16.5Z" />
  </svg>
);

export const ChevronUpIcon = ({ size = 16 }: IconProps) => (
  <svg viewBox="0 0 24 24" width={size} height={size} aria-hidden="true" {...stroke} strokeWidth={2}>
    <path d="m5.5 14.5 6.5-6.5 6.5 6.5" />
  </svg>
);

/** Stacked map layers: the routing-weight overlay toggle. */
export const LayersIcon = ({ size = 20 }: IconProps) => (
  <svg viewBox="0 0 24 24" width={size} height={size} aria-hidden="true" {...stroke}>
    <path d="M12 3.6 20.6 8.4 12 13.2 3.4 8.4Z" />
    <path d="M20.6 12.5 12 17.3 3.4 12.5" />
    <path d="M20.6 16.5 12 21.3 3.4 16.5" />
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
