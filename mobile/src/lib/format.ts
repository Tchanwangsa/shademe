import type { ConditionCode, RouteOption, Segment } from './api';

export const minutes = (m: number) => `${Math.round(m)} min`;

export const distance = (m: number) =>
  m >= 1000 ? `${(m / 1000).toFixed(2)} km` : `${Math.round(m)} m`;

export const seconds = (s: number) => {
  const v = Math.round(s);
  if (Math.abs(v) < 60) return `${v > 0 ? '+' : ''}${v} s`;
  return `${v > 0 ? '+' : ''}${Math.round(v / 60)} min`;
};

/** degC-minutes outside the no-thermal-stress band. The dose, not the percentage. */
export const dose = (v: number) => `${Math.round(v)} °C·min`;

export const degrees = (v: number) => `${v.toFixed(1)}°`;

export const pct = (v: number) => `${Math.round(v)}%`;

export const arrival = (asOf: string, walkMinutes: number) => {
  const t = new Date(asOf);
  t.setMinutes(t.getMinutes() + Math.round(walkMinutes));
  return t.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', hour12: false });
};

/** Ionicons name for a sky state. */
export const conditionIcon: Record<ConditionCode, string> = {
  sunny: 'sunny',
  partly_cloudy: 'partly-sunny',
  cloudy: 'cloudy',
  drizzle: 'rainy-outline',
  rain: 'rainy',
};

export const conditionLabel: Record<ConditionCode, string> = {
  sunny: 'Clear',
  partly_cloudy: 'Partly cloudy',
  cloudy: 'Overcast',
  drizzle: 'Light rain',
  rain: 'Rain',
};

/** UV bands as published by ARPANSA. Anything from 3 up wants protection. */
export function uvBand(uv: number): { label: string; tone: 'low' | 'moderate' | 'high' } {
  if (uv < 3) return { label: 'Low', tone: 'low' };
  if (uv < 6) return { label: 'Moderate', tone: 'moderate' };
  if (uv < 8) return { label: 'High', tone: 'high' };
  if (uv < 11) return { label: 'Very high', tone: 'high' };
  return { label: 'Extreme', tone: 'high' };
}

export type Exposure = 'sun' | 'shade' | 'indoor';

/** What a segment counts as for the exposure bar and the map line.
 *
 * `indoor` covers both true indoor ways and covered arcades: both are protected from
 * the beam, and the engine prices them the same way. Outdoors, the split is on the
 * shade fraction the raster gave the edge, at the halfway mark.
 */
export function exposureOf(s: Segment): Exposure {
  if (s.indoor || s.covered) return 'indoor';
  return s.shade >= 0.5 ? 'shade' : 'sun';
}

export interface ExposureSlice {
  exposure: Exposure;
  length: number;
}

/** Merge consecutive segments that read the same, so the bar has few, legible bands. */
export function exposureSlices(option: RouteOption): ExposureSlice[] {
  const out: ExposureSlice[] = [];
  for (const s of option.segments) {
    const e = exposureOf(s);
    const last = out[out.length - 1];
    if (last && last.exposure === e) last.length += s.length;
    else out.push({ exposure: e, length: s.length });
  }
  return out;
}
