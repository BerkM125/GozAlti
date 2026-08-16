import { MAP } from "./mapStyle.ts";

/**
 * Hex mirror of theme.css's meaning colours, because MapLibre paint properties
 * cannot read CSS variables. Change a colour there and here together, or the
 * map and the chrome will disagree.
 *
 * Green is the recommendation and appears nowhere else on the map; the direct
 * route is grey because it is a reference, not a suggestion. A white casing
 * under the green keeps it readable over pale roads.
 */
export const ROUTE = {
  safer: "#34C759", /* --recommend */
  direct: "#8E8E93", /* systemGray, deliberately quiet */
  flag: "#FF9500", /* --flag */
  casing: "#FFFFFF",
} as const;

/** Haze toward the horizon, so a tilted view has depth instead of a hard edge. */
export const SKY = {
  "sky-color": "#C8DCEC",
  "horizon-color": "#EDEFF0",
  "fog-color": MAP.land,
  "fog-ground-blend": 0.55,
} as const;
