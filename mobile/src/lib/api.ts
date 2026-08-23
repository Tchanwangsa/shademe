import { API_BASE } from './config';

export type ConditionCode = 'sunny' | 'partly_cloudy' | 'cloudy' | 'drizzle' | 'rain';
export type OptionLabel = 'Coolest' | 'Balanced' | 'Shortest';

export interface Conditions {
  as_of: string;
  hour: number;
  /** True when the wall clock fell outside 06:00-20:00 and was pulled into it. */
  clamped: boolean;
  /** The day actually priced. Today unless SHADEME_SUMMER_DATE pins it. */
  date: string;
  is_today: boolean;
  temperature: number;
  apparent_temperature: number;
  uv_index: number;
  condition: ConditionCode;
  cloud_cover: number;
  precipitation: number;
  wind_speed: number;
  relative_humidity: number;
  direct_radiation: number;
  /** What Open-Meteo actually said, before the fitted level correction. */
  temperature_raw: number;
  bias_mode: string;
  ta_bias_offset: number;
  rh_is_fallback: boolean;
  source: string;
}

export interface Summary {
  distance_m: number;
  sun_m: number;
  sun_pct: number;
  indoor_m: number;
  indoor_pct: number;
  minutes: number;
  heat_load: number;
  climb_m: number;
  doors: number;
  sun_minutes: number;
  /** degC-minutes outside the 9..26 no-stress band. The headline quantity. */
  stress_load?: number;
  heat_stress?: number;
  cold_stress?: number;
  exposed_m?: number;
  utci_peak?: number | null;
  /** Outdoor-only, so an air-conditioned arcade cannot read as free comfort. */
  utci_mean_outdoor?: number | null;
  utci_mean?: number;
  mrt_mean?: number;
}

export interface Avoided {
  stress_load_avoided: number;
  stress_load_avoided_pct: number | null;
  heat_stress_avoided: number;
  heat_stress_avoided_pct: number | null;
  cold_stress_avoided: number;
  cold_stress_avoided_pct: number | null;
  extra_m: number;
  extra_s: number;
  utci_outdoor_delta: number | null;
}

export interface Segment {
  coords: [number, number][];
  indoor: boolean;
  covered: boolean;
  shade: number;
  length: number;
}

export interface RouteOption {
  id: string;
  label: OptionLabel;
  K: number;
  K_effective: number;
  K_reached: number[];
  is_shortest: boolean;
  detour_ratio: number;
  detour_capped: boolean;
  relax_attempts: number;
  door_m: number;
  level_jump_m: number;
  summary: Summary;
  avoided: Avoided;
  geometry: { type: 'LineString'; coordinates: [number, number][] };
  segments: Segment[];
}

export interface RoutesMeta {
  snap_m: [number, number];
  hour: number;
  as_of: string;
  k_ladder: number[];
  distinct_paths: number;
  availability: { dow: string; hour: number; closed_classes: string[]; enforced: boolean };
  detour_cap: number;
  provenance: string | null;
  ms: number;
}

export interface RoutesResponse {
  conditions: Conditions;
  options: RouteOption[];
  meta: RoutesMeta;
}

export interface Place {
  name: string;
  lat: number;
  lon: number;
}

export class ApiError extends Error {
  constructor(message: string, readonly status?: number) {
    super(message);
    this.name = 'ApiError';
  }
}

async function get<T>(path: string, signal?: AbortSignal): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${API_BASE}${path}`, { signal });
  } catch (e) {
    // The overwhelmingly likely cause on a phone is API_BASE still pointing at
    // localhost, or uvicorn bound to 127.0.0.1. Say that instead of "Network request
    // failed", which sends people looking at their wifi.
    throw new ApiError(`Cannot reach the engine at ${API_BASE}. Check EXPO_PUBLIC_API_BASE is this Mac's LAN address and that uvicorn is running with --host 0.0.0.0.`);
  }
  if (!res.ok) {
    const body = await res.text().catch(() => '');
    let detail = body;
    try {
      detail = JSON.parse(body).detail ?? body;
    } catch {}
    throw new ApiError(detail || `${res.status} from ${path}`, res.status);
  }
  return res.json() as Promise<T>;
}

export const api = {
  places: (signal?: AbortSignal) => get<Place[]>('/places', signal),
  conditions: (signal?: AbortSignal) => get<Conditions>('/conditions', signal),
  routes: (from: Place, to: Place, signal?: AbortSignal) =>
    get<RoutesResponse>(
      `/routes?from_lat=${from.lat}&from_lon=${from.lon}&to_lat=${to.lat}&to_lon=${to.lon}`,
      signal,
    ),
};
