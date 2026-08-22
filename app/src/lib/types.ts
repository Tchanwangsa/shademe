/**
 * TypeScript mirror of the frozen Laneway API contract (CONTRACT.md).
 *
 * Anything the live server sends that the contract does not promise is marked
 * optional here, so the frontend never depends on a field that might vanish.
 */

export type Mode = "summer" | "winter";
export type RouteKind = "shaded" | "shortest";

/** Hours the shadow model covers, local Melbourne time. */
export const HOURS = [6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20] as const;

/** Peak of the demo day: 32.0C, cloudless, W_heat 2.12. The strongest frame. */
export const DEFAULT_HOUR = 14;

export interface Place {
  name: string;
  lat: number;
  lon: number;
}

export interface Summary {
  distance_m: number;
  sun_m: number;
  sun_pct: number;
  indoor_m: number;
  indoor_pct: number;
  minutes: number;
  heat_load: number;
}

/** One run of consecutive edges sharing the same indoor/covered flags. */
export interface Segment {
  coords: [number, number][];
  indoor: boolean;
  covered: boolean;
  shade: number;
  length: number;
}

export interface LineStringFeature {
  type: "Feature";
  geometry: { type: "LineString"; coordinates: [number, number][] };
  properties: Record<string, unknown>;
}

export interface Route {
  /** Optional here: the bundled fixtures drop it, since `segments` covers the
   *  same geometry and is what the map actually draws. */
  geojson?: LineStringFeature;
  summary: Summary;
  segments: Segment[];
}

export interface Weather {
  apparent_temperature: number;
  direct_radiation: number;
  diffuse_radiation: number;
  cloud_cover: number;
  uv_index: number;
  precipitation: number;
  wind_speed: number;
  direct_fraction: number;
  w_heat: number;
  w_wet: number;
  source: string;
  /** Live server sends dry-bulb temperature too; not in the contract. */
  temperature?: number;
}

export interface RouteResponse {
  routes: Record<RouteKind, Route>;
  weather: Weather;
  hour: number;
  /** Live server debug block (snap distances, detour ratio, timing). */
  meta?: {
    snap_m?: [number, number];
    w_heat_requested?: number;
    w_heat_effective?: number;
    detour_ratio?: number;
    detour_capped?: boolean;
    relax_attempts?: number;
    ms?: number;
  };
}

export interface HealthResponse {
  ok: boolean;
  edges: number;
  hours: number[];
  nodes?: number;
  places?: number;
  graph_source?: string;
}

/** `/static/shade_bounds.json`. `coordinates` are the four true grid corners. */
export interface ShadeBounds {
  west: number;
  south: number;
  east: number;
  north: number;
  coordinates?: [number, number][];
  hours?: number[];
}

/** Four [lon,lat] corners in MapLibre image-source order: nw, ne, se, sw. */
export type ImageCorners = [
  [number, number],
  [number, number],
  [number, number],
  [number, number],
];

export interface ShadeSource {
  /** "live" when served by the API, "bundled" when read from /public. */
  origin: "live" | "bundled";
  frame: (hour: number) => string;
  coordinates: ImageCorners;
}

/** Everything a render pass needs, plus provenance. */
export interface RouteState {
  data: RouteResponse;
  isMock: boolean;
}
