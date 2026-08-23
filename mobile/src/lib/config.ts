/** Where the FastAPI engine lives.
 *
 * A phone on the same wifi cannot reach `localhost` -- that resolves to the phone. The
 * base has to be the dev machine's LAN address, set in `.env` as EXPO_PUBLIC_API_BASE,
 * and uvicorn has to be bound to 0.0.0.0 rather than the default 127.0.0.1.
 */
export const API_BASE = process.env.EXPO_PUBLIC_API_BASE ?? 'http://localhost:8011';

/** Melbourne CBD. The graph does not extend past this by much. */
export const CBD_CENTER: [number, number] = [144.9631, -37.8136];

/** CARTO's free basemaps: no API key, attribution required and rendered in the map. */
export const MAP_STYLE = {
  light: 'https://basemaps.cartocdn.com/gl/positron-gl-style/style.json',
  dark: 'https://basemaps.cartocdn.com/gl/dark-matter-gl-style/style.json',
};
