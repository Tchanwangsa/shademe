/** Formatting and derived-metric helpers shared by the panels. */

import type { Mode, Route, RouteResponse, Segment, Weather } from "./types";

export const pad2 = (n: number) => String(n).padStart(2, "0");

/** "1.36 km" above a kilometre, "212 m" below it. */
export function fmtMetres(m: number): string {
  if (!Number.isFinite(m)) return "—";
  return m >= 1000 ? `${(m / 1000).toFixed(2)} km` : `${Math.round(m)} m`;
}

/** Signed percentage with a true minus sign, e.g. "+12%" / "−68%". */
export function fmtSignedPct(v: number): string {
  const sign = v >= 0 ? "+" : "−";
  return `${sign}${Math.abs(Math.round(v))}%`;
}

function lengthOf(s: Segment): number {
  if (Number.isFinite(s.length)) return s.length;
  let d = 0;
  const R = 6371000;
  const rad = Math.PI / 180;
  for (let i = 1; i < s.coords.length; i++) {
    const a = s.coords[i - 1];
    const b = s.coords[i];
    const x = (b[0] - a[0]) * rad * Math.cos(((a[1] + b[1]) / 2) * rad);
    const y = (b[1] - a[1]) * rad;
    d += R * Math.hypot(x, y);
  }
  return d;
}

/** Metres neither indoors nor under cover — what rain and wind reach. */
export function exposedMetres(route: Route): number {
  return route.segments
    .filter((s) => !s.indoor && !s.covered)
    .reduce((acc, s) => acc + lengthOf(s), 0);
}

/** Metres under an awning or colonnade but still outdoors. */
export function coveredMetres(route: Route): number {
  return route.segments
    .filter((s) => s.covered && !s.indoor)
    .reduce((acc, s) => acc + lengthOf(s), 0);
}

export const clamp = (v: number, lo: number, hi: number) =>
  Math.min(hi, Math.max(lo, v));

/* ------------------------------------------------------------- verdict -- */

/**
 * What the engine decided, and why.
 *
 * At both ends of the demo day the router returns the shortest path — for two
 * genuinely different reasons, and the difference is the interesting part:
 *
 *  - `low-value`: the weighting is near zero. It is not hot enough, or the
 *    light is diffuse, so a shadow buys nothing and detouring is irrational.
 *  - `already-good`: the weighting is high, but the direct route is *already*
 *    shaded (low evening sun throws the buildings across the street), so there
 *    is nothing left to buy.
 *
 * Neither is a failure. Both are the cost function working, and the UI says so.
 */
export type Verdict =
  | { kind: "detour" }
  | { kind: "no-detour"; reason: "low-value" | "already-good"; shortestGoodPct: number };

/** Below this the weather weighting is effectively off. */
const WEIGHT_FLOOR = 0.3;
/** Distance difference under this reads as "the same route". */
const SAME_ROUTE_M = 2;

export function verdictFor(
  data: RouteResponse,
  mode: Mode,
  exposedPct: (r: Route) => number,
): Verdict {
  const a = data.routes.shaded.summary;
  const b = data.routes.shortest.summary;

  if (Math.abs(a.distance_m - b.distance_m) >= SAME_ROUTE_M) return { kind: "detour" };

  const w: Weather = data.weather;
  const weight = mode === "winter" ? (w.w_wet ?? 0) : (w.w_heat ?? 0);
  const shortestExposed =
    mode === "winter" ? exposedPct(data.routes.shortest) : b.sun_pct;

  return {
    kind: "no-detour",
    reason: weight < WEIGHT_FLOOR ? "low-value" : "already-good",
    shortestGoodPct: clamp(100 - shortestExposed, 0, 100),
  };
}
