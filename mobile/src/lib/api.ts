import { API_BASE } from './config';
import { isDefault, type Walker } from './walker';

export type ConditionCode =
  | 'sunny'
  | 'partly_cloudy'
  | 'cloudy'
  | 'drizzle'
  | 'rain'
  /** The sun is below the horizon. Decided by the sun's POSITION, never by cloud. */
  | 'night';
/** `Coolest` and `Warmest` are the same badge -- least degC-minutes outside the UTCI
 *  comfort band -- named for the direction the stress actually runs. On a cold night
 *  the least-stressed walk is the WARM one, and calling it Coolest contradicts the
 *  cold_stress figure printed beside it. */
export type OptionLabel = 'Coolest' | 'Warmest' | 'Balanced' | 'Shortest' | 'Least UV';
export type Objective = 'thermal' | 'uv';

export interface Conditions {
  as_of: string;
  hour: number;
  /** The day actually priced. Today unless SHADEME_SUMMER_DATE pins it. */
  date: string;
  is_today: boolean;
  temperature: number;
  apparent_temperature: number;
  /** Null when neither the live network nor the feed could answer. Show nothing then. */
  uv_index: number | null;
  /** Which branch answered: ARPANSA measurement, the feed, or a clear-sky model. */
  uv_source: string;
  uv_index_feed: number | null;
  condition: ConditionCode;
  /** Which quantity drew the glyph and its value -- the sun's elevation, the beam as a
   *  fraction of clear sky, or (last resort, and it says so) cloud cover. */
  condition_source: string;
  /** Degrees above the horizon at the hour being priced. Null without pvlib. */
  solar_elevation: number | null;
  /** Direct radiation over what a clear sky would deliver. Null when unreadable. */
  beam_fraction: number | null;
  cloud_cover: number;
  precipitation: number;
  wind_speed: number;
  relative_humidity: number;
  direct_radiation: number;
  direct_fraction: number;
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
  /** UV index-minutes collected over the walk. The UV analogue of stress_load. */
  uv_dose?: number;
  /** The same dose in standard erythemal doses; ~2 SED reddens untanned fair skin. */
  uv_sed?: number;
  uv_exposed_minutes?: number;
  /** Mean share of the open-sky UV index along the route, 0..1. Day-independent. */
  uv_mean_frac?: number | null;
  uv_peak?: number;
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
  uv_dose_avoided?: number;
  uv_sed_avoided?: number;
  uv_dose_avoided_pct?: number | null;
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
  /** Every label this option earned. Empty when it is the only option on the list. */
  labels: OptionLabel[];
  /** labels[0], or null. Kept for compatibility; render `labels`. */
  label: OptionLabel | null;
  /** Which search produced it. Both ladders feed one list. */
  objective: Objective;
  K: number;
  K_effective: number;
  reached: { kind: Objective; K: number }[];
  is_shortest: boolean;
  detour_ratio: number;
  detour_capped: boolean;
  relax_attempts: number;
  door_m: number;
  level_jump_m: number;
  /** The route priced in minutes under THIS walker's K: `minutes + K * stress_load`,
   *  plus the door price charged to every option alike. The router's own objective
   *  restated per route, which is what makes options from different searches
   *  comparable. Thermal only — UV is a second preference, not a term in this one. */
  weighted_minutes: number;
  /** Exactly one option carries this, and only when there is more than one to choose
   *  between. It is the least `weighted_minutes`, so it moves toward the cooler walk as
   *  the heat-sensitivity answers push K up. */
  recommended: boolean;
  summary: Summary;
  avoided: Avoided;
  geometry: { type: 'LineString'; coordinates: [number, number][] };
  segments: Segment[];
}

export interface RoutesMeta {
  snap_m: [number, number];
  hour: number;
  as_of: string;
  /** The thermal ladder actually searched — the base ladder scaled by the walker's
   *  K multiplier. */
  k_ladder: number[];
  /** The ladder a walker who declared nothing gets. `k_ladder` divided by
   *  `walker.k_multiplier`. */
  k_ladder_base: number[];
  k_uv_ladder: number[];
  uv_index: number | null;
  distinct_paths: number;
  dominated_dropped: number;
  near_duplicates_merged: number;
  unlabelled_dropped: number;
  availability: { dow: string; hour: number; closed_classes: string[]; enforced: boolean };
  detour_cap: number;
  /** True when the surface-temperature march was still rebuilding, so this was priced on
   * the previous one. Conditions are live either way; only the ground lags, by at most
   * one weather refresh. */
  engine_stale: boolean;
  /** The weather fetch those surface temperatures came from. */
  engine_as_of: string | null;
  provenance: string | null;
  /** Which K the ladder and the recommendation were priced under, and the two answers
   *  that produced it — echoed back rather than assumed, so a client that sent nothing
   *  can see that nothing was applied. Same job as `provenance`: no figure without the
   *  config behind it.
   *
   *  OPTIONAL BECAUSE THE SERVER MIGHT BE OLDER. A phone points at whatever API_BASE
   *  says, which during development is regularly a laptop running a build from before
   *  this field existed. Typed as present-or-absent so reading it has to be guarded,
   *  rather than typed as always-there and crashing the route sheet when it is not. */
  walker?: {
    unacclimatised: boolean;
    vulnerable: boolean;
    /** 1.0, 1.8, or 3.24. Multiplicative because the two answers are independent. */
    k_multiplier: number;
    K: number;
  };
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
  /** The muted second line -- street, suburb. */
  address?: string | null;
  /** OSM's own word for the thing: pedestrian, marketplace, station, cafe. Drives the
   * glyph in the picker. "here" for a GPS fix. */
  kind?: string | null;
  /** OSM's raw `opening_hours` tag, or null where the place carries none. */
  opening_hours?: string | null;
  /** Whether OSM's hours say it is open at the moment the server answered.
   *
   * THREE-VALUED, and null is a real answer meaning "not known" -- most OSM places carry
   * no opening_hours at all, and a street never will. Render a badge only for `false`;
   * treating null as closed would put Closed on half the CBD. */
  open_now?: boolean | null;
  /** How far the nearest walkable node is. The engine routes from there, not from the
   * pin, and for a big place like a station the two differ by a block. */
  snap_m?: number | null;
  /** Straight-line metres from whatever origin the search was given, or null when it
   * was given none. NOT the walking distance -- /routes owns that, and it is longer. */
  distance_m?: number | null;
}

export interface SearchResponse {
  query: string;
  results: Place[];
  /** Matches that fit the words but landed outside the CBD the graph covers. The
   * difference between "no such place" and "not one we can walk you to". */
  outside: number;
  source: 'photon' | 'nominatim' | 'none';
}

/** A GPS fix, named. `lat`/`lon` come back exactly as sent -- only the label is OSM's. */
export interface ReverseResponse extends Place {
  in_coverage: boolean;
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
  /** Free-text place search over OpenStreetMap, already filtered to what the engine can
   * route. A query under two characters comes back empty -- there is no curated list any
   * more, and the picker shows the device's own recent picks in that space instead.
   *
   * `near` is optional, and when it is given the results come back nearest first with a
   * distance on each. It is the only thing that separates five Hungry Jack's whose name
   * and street both read the same. */
  search: (q: string, near?: { lat: number; lon: number } | null, signal?: AbortSignal) => {
    const p = new URLSearchParams({ q });
    if (near) {
      p.set('near_lat', String(near.lat));
      p.set('near_lon', String(near.lon));
    }
    return get<SearchResponse>(`/search?${p.toString()}`, signal);
  },
  reverse: (lat: number, lon: number, signal?: AbortSignal) =>
    get<ReverseResponse>(`/reverse?lat=${lat}&lon=${lon}`, signal),
  conditions: (signal?: AbortSignal) => get<Conditions>('/conditions', signal),
  /** Walking options between two points, priced at the server's wall clock.
   *
   * `walker` is the two heat-sensitivity answers. They are sent ONLY when one of them is
   * set: an untouched dial puts nothing on the query string, so the default request is
   * byte-identical to the one this app sent before the feature existed, and a response
   * with `meta.walker.k_multiplier === 1` is provably unpersonalised rather than
   * personalised back to the default. Nothing about them is stored server-side. */
  routes: (from: Place, to: Place, walker?: Walker | null, signal?: AbortSignal) => {
    const p = new URLSearchParams({
      from_lat: String(from.lat),
      from_lon: String(from.lon),
      to_lat: String(to.lat),
      to_lon: String(to.lon),
    });
    if (walker && !isDefault(walker)) {
      if (walker.unacclimatised) p.set('unacclimatised', 'true');
      if (walker.vulnerable) p.set('vulnerable', 'true');
    }
    return get<RoutesResponse>(`/routes?${p.toString()}`, signal);
  },
};
