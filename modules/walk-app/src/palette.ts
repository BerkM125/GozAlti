import { MAP } from "./mapStyle.ts";

/**
 * Hex mirror of theme.css's meaning colours, because MapLibre paint properties
 * cannot read CSS variables. Change a colour there and here together, or the
 * map and the chrome will disagree.
 *
 * The recommended route wears the system blue (--accent) over a white casing;
 * the direct route is grey because it is a reference, not a suggestion.
 */
export const ROUTE = {
  safer: "#007AFF", /* --accent */
  direct: "#8E8E93", /* systemGray, deliberately quiet */
  casing: "#FFFFFF",
} as const;

/** The tapped block: darker than the route's blue so it reads on top of it. */
export const SELECTED = {
  line: "#0040DD",
  casing: "#FFFFFF",
} as const;

/**
 * Routing-weight ramp, mirrored in theme.css as --ramp. Tuned to the observed
 * risk spread (0.08-0.67, with 74% of blocks in 0.15-0.35) - a ramp over the
 * nominal [0,1] would paint the whole city one colour. Muted on purpose: it is
 * a data layer, never an alert, and road labels must stay readable over it.
 */
export const WEIGHT_STOPS: [number, string][] = [
  [0.08, "#4D9E58"],
  [0.16, "#93B24A"],
  [0.24, "#C9AC3F"],
  [0.32, "#D68936"],
  [0.42, "#CE5F2E"],
  [0.56, "#B23A2C"],
];

const T_LO = WEIGHT_STOPS[0][0];
const T_SPAN = 0.52; // puts the last stop at 92%, matching --ramp's gradient

/** Normalised 0-1 position of a risk value on the ramp, for scale markers. */
export const riskToT = (risk: number) =>
  Math.max(0, Math.min(1, (risk - T_LO) / T_SPAN));

const hexToRgb = (hex: string): [number, number, number] => [
  parseInt(hex.slice(1, 3), 16),
  parseInt(hex.slice(3, 5), 16),
  parseInt(hex.slice(5, 7), 16),
];

/** The ramp colour for a 0-1 score, for DOM elements MapLibre can't paint. */
export function weightColor(score: number): string {
  const stops = WEIGHT_STOPS;
  if (score <= stops[0][0]) return stops[0][1];
  for (let i = 1; i < stops.length; i++) {
    const [x1, c1] = stops[i];
    const [x0, c0] = stops[i - 1];
    if (score <= x1) {
      const t = (score - x0) / (x1 - x0);
      const a = hexToRgb(c0);
      const b = hexToRgb(c1);
      const mix = a.map((v, ch) => Math.round(v + (b[ch] - v) * t));
      return `rgb(${mix[0]} ${mix[1]} ${mix[2]})`;
    }
  }
  return stops[stops.length - 1][1];
}

/** Haze toward the horizon, so a tilted view has depth instead of a hard edge. */
export const SKY = {
  "sky-color": "#C8DCEC",
  "horizon-color": "#EDEFF0",
  "fog-color": MAP.land,
  "fog-ground-blend": 0.55,
} as const;
