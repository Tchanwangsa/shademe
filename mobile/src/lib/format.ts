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

/** UV dose over a walk, in index-minutes. The UV analogue of `dose`. */
export const uvDose = (v: number) => `${v.toFixed(1)} UV·min`;

/** The WHO / ARPANSA UV colour scale, which is the whole readout.
 *
 * The band NAME is deliberately not returned. "UV 6" next to "High" says one thing
 * twice, and the scale is a colour scale precisely so it does not need the word -- it is
 * the same green/yellow/orange/red/violet on every sunscreen bottle and weather app in
 * the country. The number carries the value and the colour carries the severity.
 *
 * Darker variants for dark mode: the published yellow is unreadable on a dark card, so
 * each band is given a light-background and a dark-background colour rather than
 * dropping the scale.
 */
const UV_SCALE: { max: number; light: string; dark: string }[] = [
  { max: 3, light: '#3D8B3D', dark: '#6FCF6F' },   // low
  { max: 6, light: '#B58500', dark: '#F2C94C' },   // moderate
  { max: 8, light: '#D2691E', dark: '#F2994A' },   // high
  { max: 11, light: '#C7351F', dark: '#EB5757' },  // very high
  { max: Infinity, light: '#8E44AD', dark: '#BB6BD9' }, // extreme
];

export function uvColor(uv: number, isDark: boolean): string {
  const band = UV_SCALE.find((b) => uv < b.max) ?? UV_SCALE[UV_SCALE.length - 1];
  return isDark ? band.dark : band.light;
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
