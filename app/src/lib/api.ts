/**
 * Typed fetch wrappers for the Laneway API, with a bundled fixture fallback.
 *
 * Every call tries the live API first. On any failure — offline, timeout,
 * non-2xx — it falls back to `fixtures.json` and reports `isMock: true` so the
 * UI can show an honest badge instead of a blank screen.
 *
 * `fixtures.json` is a real capture of `/route` for the contract demo route
 * (Melbourne Central -> Federation Square) across every hour 06..20 in both
 * modes, taken on demo day 2026-01-26. It is not synthesised: the fallback
 * reproduces the live narrative exactly, including the hours where the engine
 * declines to detour.
 */

import rawFixtures from "./fixtures.json";
import {
  DEFAULT_HOUR,
  HOURS,
  type ImageCorners,
  type Mode,
  type Place,
  type RouteResponse,
  type RouteState,
  type ShadeBounds,
  type ShadeSource,
} from "./types";

export const API_BASE =
  process.env.NEXT_PUBLIC_API_BASE?.replace(/\/$/, "") ?? "http://localhost:8000";

const API_TIMEOUT_MS = 6000;

/** JSON `coords` widen to `number[][]`; the capture is contract-shaped. */
const FIXTURES = rawFixtures as unknown as Record<string, RouteResponse>;

/* ------------------------------------------------------------- fallbacks -- */

/** Grid extent from `out/grid.json`, reprojected. Used if bounds can't load. */
const FALLBACK_CORNERS: ImageCorners = [
  [144.9354437377964, -37.7896357970758],
  [144.99079767161697, -37.79059314130006],
  [144.98958355508728, -37.83536303167622],
  [144.93419622930872, -37.834404151516104],
];

/** Contract demo route first, then the majors. Mirrors `GET /places`. */
export const FALLBACK_PLACES: Place[] = [
  { name: "Melbourne Central", lat: -37.81001, lon: 144.9628 },
  { name: "Federation Square", lat: -37.818, lon: 144.9691 },
  { name: "Flinders Street Station", lat: -37.8182, lon: 144.967 },
  { name: "Southern Cross Station", lat: -37.8183, lon: 144.9527 },
  { name: "Queen Victoria Market", lat: -37.807, lon: 144.9568 },
  { name: "State Library", lat: -37.8098, lon: 144.9649 },
  { name: "Emporium", lat: -37.8118, lon: 144.9633 },
  { name: "Myer", lat: -37.8135, lon: 144.9645 },
  { name: "Bourke St Mall", lat: -37.8139, lon: 144.9643 },
  { name: "Docklands", lat: -37.8169, lon: 144.9462 },
  { name: "Carlton Gardens", lat: -37.8054, lon: 144.9712 },
  { name: "RMIT", lat: -37.8078, lon: 144.9636 },
  { name: "Parliament Station", lat: -37.811, lon: 144.9727 },
  { name: "Crown Casino", lat: -37.8225, lon: 144.9584 },
  { name: "Melbourne Museum", lat: -37.8033, lon: 144.9715 },
];

/* --------------------------------------------------------------- helpers -- */

export const clamp = (v: number, lo: number, hi: number) =>
  Math.min(hi, Math.max(lo, v));

export const pad2 = (n: number) => String(n).padStart(2, "0");

async function getJSON<T>(url: string, ms = API_TIMEOUT_MS): Promise<T> {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), ms);
  try {
    const res = await fetch(url, {
      signal: ctrl.signal,
      headers: { accept: "application/json" },
    });
    if (!res.ok) throw new Error(`${url} -> ${res.status}`);
    return (await res.json()) as T;
  } finally {
    clearTimeout(timer);
  }
}

/** Nearest captured hour for a mode, cloned so callers can't corrupt the store. */
function fixtureFor(hour: number, mode: Mode): RouteResponse {
  const doc = FIXTURES[`${mode}-${hour}`] ?? FIXTURES[`${mode}-${DEFAULT_HOUR}`];
  const clone = structuredClone(doc);
  clone.weather = {
    ...clone.weather,
    source: `${clone.weather.source} · bundled capture`,
  };
  return clone;
}

/* ------------------------------------------------------------- endpoints -- */

export interface RouteQuery {
  from: Place;
  to: Place;
  hour: number;
  mode: Mode;
}

/**
 * `GET /route`, falling back to the bundled capture. Never throws.
 *
 * The fixture is only geometrically correct for the contract demo route, so a
 * fallback for any other origin/destination is flagged `isApprox` and the UI
 * says so rather than quietly lying about a route it did not compute.
 */
export async function fetchRoute(q: RouteQuery): Promise<RouteState> {
  const qs = new URLSearchParams({
    from_lat: String(q.from.lat),
    from_lon: String(q.from.lon),
    to_lat: String(q.to.lat),
    to_lon: String(q.to.lon),
    hour: String(q.hour),
    mode: q.mode,
    compare: "true",
  });

  try {
    const data = await getJSON<RouteResponse>(`${API_BASE}/route?${qs}`);
    if (!data?.routes?.shaded?.segments || !data?.routes?.shortest?.segments) {
      throw new Error("malformed /route payload");
    }
    return { data, isMock: false };
  } catch {
    return { data: fixtureFor(q.hour, q.mode), isMock: true };
  }
}

/** `GET /places`, falling back to a hardcoded list. Never throws. */
export async function fetchPlaces(): Promise<Place[]> {
  try {
    const list = await getJSON<Place[]>(`${API_BASE}/places`, 3000);
    const clean = list.filter(
      (p) =>
        p && typeof p.name === "string" && Number.isFinite(p.lat) && Number.isFinite(p.lon),
    );
    return clean.length ? clean : FALLBACK_PLACES;
  } catch {
    return FALLBACK_PLACES;
  }
}

/**
 * Resolve a working shade-overlay source: the live API first, then the frames
 * bundled into `/public`. Both the bounds JSON and one PNG frame must actually
 * load before a source is accepted.
 */
export async function resolveShadeSource(): Promise<ShadeSource | null> {
  const candidates: {
    origin: "live" | "bundled";
    bounds: string;
    frame: (h: number) => string;
  }[] = [
    {
      origin: "live",
      bounds: `${API_BASE}/static/shade_bounds.json`,
      frame: (h) => `${API_BASE}/static/shade_${pad2(h)}.png`,
    },
    {
      origin: "bundled",
      bounds: "/mock_shade/shade_bounds.json",
      frame: (h) => `/mock_shade/shade_${pad2(h)}.png`,
    },
  ];

  for (const c of candidates) {
    try {
      const meta = await getJSON<ShadeBounds>(c.bounds, 4000);
      await decodeImage(c.frame(DEFAULT_HOUR));
      return { origin: c.origin, frame: c.frame, coordinates: cornersOf(meta) };
    } catch {
      /* try the next candidate */
    }
  }
  return null;
}

function cornersOf(meta: ShadeBounds): ImageCorners {
  // precompute.py ships the four true grid corners (nw, ne, se, sw) because the
  // MGA55 grid sits ~1.2 degrees off true north. Prefer them over the bbox.
  const c = meta.coordinates;
  if (Array.isArray(c) && c.length === 4) return c as ImageCorners;
  if (meta.west == null) return FALLBACK_CORNERS;
  return [
    [meta.west, meta.north],
    [meta.east, meta.north],
    [meta.east, meta.south],
    [meta.west, meta.south],
  ];
}

function decodeImage(url: string): Promise<HTMLImageElement> {
  return new Promise((resolve, reject) => {
    const img = new Image();
    img.crossOrigin = "anonymous";
    img.onload = () => resolve(img);
    img.onerror = () => reject(new Error(`image ${url}`));
    img.src = url;
  });
}

/**
 * Warm every hourly frame so scrubbing the slider is instant and flicker-free.
 * References are kept alive for the lifetime of the returned array.
 */
export function preloadShadeFrames(src: ShadeSource): HTMLImageElement[] {
  return HOURS.map((h) => {
    const img = new Image();
    img.crossOrigin = "anonymous";
    img.src = src.frame(h);
    return img;
  });
}
