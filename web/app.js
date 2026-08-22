/* ============================================================================
   Laneway — weather-aware pedestrian routing, Melbourne CBD
   Vanilla JS. No build step. MapLibre GL JS from CDN.
   ========================================================================== */
'use strict';

/* ---------------------------------------------------------------- config -- */

const API_BASE = (location.port === '8000' || location.port === '')
  ? '' : 'http://localhost:8000';

/** 'auto' = try the live API, silently fall back to mock.json on any failure.
 *  true   = always mock.  false = never mock (surface the error). */
const USE_MOCK = 'auto';

const API_TIMEOUT_MS = 3500;
const DEBOUNCE_MS = 220;

const HOURS = [6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20];
const DEFAULT_HOUR = 16;

/* Fallback CBD extent, used only if no shade_bounds.json can be reached.
   Matches out/grid.json reprojected to WGS84. */
const FALLBACK_BOUNDS = { west: 144.93420, south: -37.83536, east: 144.99080, north: -37.78964 };

/* Fallback place list — CONTRACT demo route first, then majors. */
const FALLBACK_PLACES = [
  { name: 'Melbourne Central',      lat: -37.81001, lon: 144.96280 },
  { name: 'Federation Square',      lat: -37.81800, lon: 144.96910 },
  { name: 'Flinders Street Station',lat: -37.81830, lon: 144.96690 },
  { name: 'Southern Cross Station', lat: -37.81830, lon: 144.95270 },
  { name: 'State Library Victoria', lat: -37.80980, lon: 144.96490 },
  { name: 'Emporium Melbourne',     lat: -37.81180, lon: 144.96380 },
  { name: 'Bourke Street Mall',     lat: -37.81370, lon: 144.96450 },
  { name: 'Queen Victoria Market',  lat: -37.80700, lon: 144.95680 },
  { name: 'RMIT City Campus',       lat: -37.80800, lon: 144.96370 },
  { name: 'Degraves Street',        lat: -37.81680, lon: 144.96650 },
  { name: 'Carlton Gardens',        lat: -37.80530, lon: 144.97140 },
  { name: 'Marvel Stadium',         lat: -37.81650, lon: 144.94740 },
  { name: 'Crown / Southbank',      lat: -37.82250, lon: 144.95890 }
];

/* Demo-day (14 Jan) hourly weather, used ONLY when running on mock data so that
   scrubbing the hour slider moves the radiation split. The 10:00 and 16:00 rows
   are the real Open-Meteo values quoted in TECHNICAL_PLAN.md. */
const DEMO_WX = {
  6:  [18.1,  12,  40, 45, 0.2], 7:  [19.6,  88,  96, 40, 0.9],
  8:  [21.4, 210, 132, 30, 2.0], 9:  [23.5, 336, 158, 22, 3.4],
  10: [25.6,  88, 345,100, 4.1], 11: [27.2, 214, 322, 78, 5.2],
  12: [28.9, 402, 268, 45, 6.6], 13: [30.1, 512, 214, 20, 7.6],
  14: [31.0, 566, 186, 10, 7.9], 15: [31.4, 588, 170,  5, 7.6],
  16: [31.2, 558, 160,  3, 7.2], 17: [30.2, 452, 148,  4, 5.8],
  18: [28.4, 306, 128,  8, 3.9], 19: [26.1, 152,  96, 12, 2.0],
  20: [23.4,  34,  52, 18, 0.6]
};

/* Mean modelled shade over the CBD per hour (from the precomputed frames).
   Used in mock mode to scale sun-exposed metres as you scrub. */
const SHADE_MEAN = {
  6: 1.000, 7: 0.633, 8: 0.525, 9: 0.460, 10: 0.415, 11: 0.368, 12: 0.247,
  13: 0.185, 14: 0.184, 15: 0.251, 16: 0.372, 17: 0.416, 18: 0.460,
  19: 0.528, 20: 0.654
};

/* Winter demo-day conditions (22 Aug), used only in mock mode. */
const DEMO_WX_WINTER = {
  apparent_temperature: 6.8, direct_radiation: 96, diffuse_radiation: 148,
  cloud_cover: 92, uv_index: 1.1, precipitation: 1.8, wind_speed: 26.0,
  source: 'demo-day 2026-08-22 (mock)'
};

/* ----------------------------------------------------------------- state -- */

const state = {
  mode: 'summer',
  hour: DEFAULT_HOUR,
  from: { ...FALLBACK_PLACES[0] },
  to:   { ...FALLBACK_PLACES[1] },
  places: FALLBACK_PLACES.slice(),
  mocked: null,          // null = unknown yet, true/false once resolved
  arm: null,             // 'from' | 'to' when map-picking is armed
  shade: null,           // { frameUrl(h), coordinates }
  data: null,
  reqSeq: 0
};

const $ = (id) => document.getElementById(id);
const pad = (n) => String(n).padStart(2, '0');
const fmtM = (m) => (m >= 1000 ? (m / 1000).toFixed(2) + ' km' : Math.round(m) + ' m');
const pct = (v) => (v >= 0 ? '+' : '−') + Math.abs(Math.round(v)) + '%';

/* ================================================================= MAP == */

const map = new maplibregl.Map({
  container: 'map',
  attributionControl: false,
  style: {
    version: 8,
    sources: {
      carto: {
        type: 'raster',
        tiles: [
          'https://a.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{ratio}.png',
          'https://b.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{ratio}.png',
          'https://c.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{ratio}.png'
        ].map((u) => u.replace('{ratio}', devicePixelRatio > 1.4 ? '@2x' : '')),
        tileSize: 256,
        maxzoom: 20,
        attribution:
          '&copy; <a href="https://carto.com/attributions">CARTO</a> &copy; ' +
          '<a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
      }
    },
    layers: [
      { id: 'bg', type: 'background', paint: { 'background-color': '#080b12' } },
      { id: 'carto', type: 'raster', source: 'carto',
        paint: { 'raster-opacity': 0.78, 'raster-saturation': -0.15, 'raster-contrast': 0.08 } }
    ]
  },
  center: [144.96560, -37.81400],
  zoom: 14.5,
  minZoom: 11,
  maxZoom: 19,
  pitch: 0,
  dragRotate: false
});

map.addControl(new maplibregl.AttributionControl({ compact: true }), 'bottom-right');
map.addControl(new maplibregl.NavigationControl({ showCompass: false }), 'top-left');
map.touchZoomRotate.disableRotation();

/* ---------------------------------------------------------- shade layer -- */

/* Candidate sources, in priority order. Each is validated (bounds JSON must
   parse AND one PNG frame must actually decode) before it is used. */
const SHADE_SOURCES = [
  { name: 'live', bounds: API_BASE + '/static/shade_bounds.json',
    frame: (h) => API_BASE + '/static/shade_' + pad(h) + '.png' },
  { name: 'mock', bounds: 'mock_shade/shade_bounds.json',
    frame: (h) => 'mock_shade/shade_' + pad(h) + '.png' }
];

function loadImage(url) {
  return new Promise((res, rej) => {
    const img = new Image();
    img.crossOrigin = 'anonymous';
    img.onload = () => res(img);
    img.onerror = () => rej(new Error('img ' + url));
    img.src = url;
  });
}

function boundsToCoordinates(meta) {
  // precompute.py ships the 4 true grid corners (nw, ne, se, sw) because the
  // MGA55 grid is ~1.2 deg off true north. Prefer them; fall back to the bbox.
  if (Array.isArray(meta && meta.coordinates) && meta.coordinates.length === 4) {
    return meta.coordinates;
  }
  const b = meta && meta.west != null ? meta : FALLBACK_BOUNDS;
  return [[b.west, b.north], [b.east, b.north], [b.east, b.south], [b.west, b.south]];
}

async function resolveShadeSource() {
  for (const src of SHADE_SOURCES) {
    try {
      const r = await fetch(src.bounds, { cache: 'force-cache' });
      if (!r.ok) throw new Error(r.status);
      const meta = await r.json();
      await loadImage(src.frame(DEFAULT_HOUR));       // prove the frames exist
      return { name: src.name, frame: src.frame, coordinates: boundsToCoordinates(meta) };
    } catch (e) { /* try the next source */ }
  }
  return null;
}

function preloadFrames(src) {
  // Hold references so the browser cache keeps them warm — scrubbing the hour
  // slider then swaps images with no network round trip and no flicker.
  window.__shadeFrames = HOURS.map((h) => {
    const img = new Image();
    img.src = src.frame(h);
    return img;
  });
}

function addShadeLayer() {
  if (!state.shade) return;
  map.addSource('shade', {
    type: 'image',
    url: state.shade.frame(state.hour),
    coordinates: state.shade.coordinates
  });
  map.addLayer({
    id: 'shade-layer',
    type: 'raster',
    source: 'shade',
    paint: {
      'raster-opacity': +$('opacitySlider').value / 100,
      'raster-fade-duration': 0,          // instant swap, no cross-fade flicker
      'raster-resampling': 'linear',
      // The shipped frames are a low-alpha navy wash — near-invisible on a dark
      // basemap. Lift and cool them so the shadow field actually reads on video.
      'raster-brightness-min': 0.48,
      'raster-brightness-max': 1,
      'raster-saturation': 0.35,
      'raster-contrast': 0.2
    }
  }, 'route-short-casing');
}

function updateShadeFrame() {
  const s = map.getSource('shade');
  if (!s || !state.shade) return;
  s.updateImage({ url: state.shade.frame(state.hour), coordinates: state.shade.coordinates });
}

/* --------------------------------------------------------- route layers -- */

const EMPTY = { type: 'FeatureCollection', features: [] };

function addRouteLayers() {
  ['route-short', 'route-shade', 'route-covered', 'route-indoor'].forEach((id) => {
    map.addSource(id, { type: 'geojson', data: EMPTY });
  });

  // -- shortest (what mapping apps give you): muted, recessive
  // Offset it so that where the two routes coincide you still see both ribbons.
  map.addLayer({
    id: 'route-short-casing', type: 'line', source: 'route-short',
    layout: { 'line-cap': 'round', 'line-join': 'round' },
    paint: { 'line-color': '#05080d', 'line-width': 9, 'line-opacity': 0.6,
             'line-blur': 1, 'line-offset': 4 }
  });
  map.addLayer({
    id: 'route-short-line', type: 'line', source: 'route-short',
    layout: { 'line-cap': 'round', 'line-join': 'round' },
    paint: { 'line-color': '#98a5b8', 'line-width': 4.5, 'line-opacity': 0.92,
             'line-offset': 4 }
  });

  // -- our route: outdoor legs
  map.addLayer({
    id: 'route-shade-glow', type: 'line', source: 'route-shade',
    layout: { 'line-cap': 'round', 'line-join': 'round' },
    paint: { 'line-color': '#5ef2c0', 'line-width': 17, 'line-opacity': 0.20, 'line-blur': 12 }
  });
  map.addLayer({
    id: 'route-shade-line', type: 'line', source: 'route-shade',
    layout: { 'line-cap': 'round', 'line-join': 'round' },
    paint: { 'line-color': '#5ef2c0', 'line-width': 5.5 }
  });

  // -- covered (awning / verandah / colonnade)
  map.addLayer({
    id: 'route-covered-line', type: 'line', source: 'route-covered',
    layout: { 'line-cap': 'butt', 'line-join': 'round' },
    paint: {
      'line-color': '#9fe6c8', 'line-width': 5,
      'line-dasharray': [3, 1.6], 'line-opacity': 0.95
    }
  });

  // -- indoor (arcades, subways, building pass-throughs): the money shot
  map.addLayer({
    id: 'route-indoor-glow', type: 'line', source: 'route-indoor',
    layout: { 'line-cap': 'round', 'line-join': 'round' },
    paint: { 'line-color': '#ffc861', 'line-width': 22, 'line-opacity': 0.32, 'line-blur': 14 }
  });
  map.addLayer({
    id: 'route-indoor-halo', type: 'line', source: 'route-indoor',
    layout: { 'line-cap': 'round', 'line-join': 'round' },
    paint: { 'line-color': '#ffc861', 'line-width': 9, 'line-opacity': 0.22, 'line-blur': 3 }
  });
  map.addLayer({
    id: 'route-indoor-line', type: 'line', source: 'route-indoor',
    layout: { 'line-cap': 'butt', 'line-join': 'round' },
    paint: { 'line-color': '#ffd98a', 'line-width': 6, 'line-dasharray': [1.6, 1.1] }
  });
}

/* Flowing dash on the indoor legs — reads as "you are moving through here". */
const DASH_SEQ = [
  [0, 4, 3], [0.5, 4, 2.5], [1, 4, 2], [1.5, 4, 1.5], [2, 4, 1], [2.5, 4, 0.5],
  [3, 4, 0], [0, 0.5, 3, 3.5], [0, 1, 3, 3], [0, 1.5, 3, 2.5], [0, 2, 3, 2],
  [0, 2.5, 3, 1.5], [0, 3, 3, 1], [0, 3.5, 3, 0.5]
];
let dashStep = 0;
function animateDash() {
  if (map.getLayer('route-indoor-line')) {
    map.setPaintProperty('route-indoor-line', 'line-dasharray', DASH_SEQ[dashStep]);
  }
  dashStep = (dashStep + 1) % DASH_SEQ.length;
}

/* -------------------------------------------------------------- markers -- */

function makePin(cls, label) {
  const el = document.createElement('div');
  el.className = 'pin ' + cls;
  el.textContent = label;
  return el;
}

const markerA = new maplibregl.Marker({ element: makePin('pin-a', 'A'), draggable: true })
  .setLngLat([state.from.lon, state.from.lat]);
const markerB = new maplibregl.Marker({ element: makePin('pin-b', 'B'), draggable: true })
  .setLngLat([state.to.lon, state.to.lat]);

markerA.on('dragend', () => {
  const p = markerA.getLngLat();
  setPoint('from', { name: 'Dropped pin', lat: p.lat, lon: p.lng });
});
markerB.on('dragend', () => {
  const p = markerB.getLngLat();
  setPoint('to', { name: 'Dropped pin', lat: p.lat, lon: p.lng });
});

/* =============================================================== DATA == */

function withTimeout(promise, ms) {
  return new Promise((res, rej) => {
    const t = setTimeout(() => rej(new Error('timeout')), ms);
    promise.then((v) => { clearTimeout(t); res(v); },
                 (e) => { clearTimeout(t); rej(e); });
  });
}

async function getJSON(url, ms) {
  const r = await withTimeout(fetch(url, { headers: { accept: 'application/json' } }),
                              ms || API_TIMEOUT_MS);
  if (!r.ok) throw new Error(url + ' -> ' + r.status);
  return r.json();
}

/* ---- geometry helpers (used to derive metrics the summary doesn't carry) -- */

function segLength(s) {
  if (typeof s.length === 'number') return s.length;
  let d = 0;
  for (let i = 1; i < s.coords.length; i++) d += haversine(s.coords[i - 1], s.coords[i]);
  return d;
}
function haversine(a, b) {
  const R = 6371000, rad = Math.PI / 180;
  const x = (b[0] - a[0]) * rad * Math.cos(((a[1] + b[1]) / 2) * rad);
  const y = (b[1] - a[1]) * rad;
  return R * Math.hypot(x, y);
}
function exposedMetres(route) {
  return (route.segments || [])
    .filter((s) => !s.indoor && !s.covered)
    .reduce((a, s) => a + segLength(s), 0);
}
function coveredMetres(route) {
  return (route.segments || [])
    .filter((s) => s.covered && !s.indoor)
    .reduce((a, s) => a + segLength(s), 0);
}

/* -------------------------------------------------- mock transformations -- */

function deepClone(o) { return JSON.parse(JSON.stringify(o)); }

/** Re-weather the mock for the requested hour so scrubbing is alive. */
function mockForHour(doc, hour, mode) {
  const d = deepClone(doc);
  const w = d.weather;

  if (mode === 'winter') {
    Object.assign(w, DEMO_WX_WINTER);
  } else {
    const row = DEMO_WX[hour] || DEMO_WX[DEFAULT_HOUR];
    w.apparent_temperature = row[0];
    w.direct_radiation = row[1];
    w.diffuse_radiation = row[2];
    w.cloud_cover = row[3];
    w.uv_index = row[4];
    w.precipitation = 0.0;
    w.wind_speed = 14.0;
    w.source = 'demo-day 2026-01-14 (mock)';
  }

  const tot = w.direct_radiation + w.diffuse_radiation;
  w.direct_fraction = tot > 0 ? +(w.direct_radiation / tot).toFixed(3) : 0;
  w.w_heat = mode === 'winter' ? 0
    : +(clamp((w.apparent_temperature - 20) / 5, 0, 3) * w.direct_fraction).toFixed(2);
  w.w_wet = mode === 'winter'
    ? +clamp(w.precipitation * 2 + w.wind_speed / 15, 0, 3).toFixed(2) : 0;
  d.hour = hour;

  // Sun-exposed metres track the shadow geometry, which moves with the hour.
  const f = (1 - (SHADE_MEAN[hour] != null ? SHADE_MEAN[hour] : SHADE_MEAN[DEFAULT_HOUR]))
          / (1 - SHADE_MEAN[DEFAULT_HOUR]);
  for (const k of ['shaded', 'shortest']) {
    const s = d.routes[k].summary;
    s.sun_m = Math.round(s.sun_m * f);
    s.sun_pct = +(100 * s.sun_m / s.distance_m).toFixed(1);
    s.heat_load = +(s.heat_load * f * (w.direct_radiation / 558)).toFixed(1);
  }

  if (mode === 'winter') winteriseRoutes(d);
  return d;
}

/** Demo-only: in winter the cost function chases cover (awnings, verandahs),
 *  not shade. The mock ships one summer geometry, so re-flag the long Bourke /
 *  Little Collins legs of our route as `covered` to show what winter optimises
 *  for. Live API responses are never touched by this. */
function winteriseRoutes(d) {
  const r = d.routes.shaded;
  const outdoor = r.segments.filter((s) => !s.indoor && !s.covered)
                            .sort((a, b) => segLength(b) - segLength(a));
  let budget = 0.62 * outdoor.reduce((a, s) => a + segLength(s), 0);
  for (const s of outdoor) {
    if (budget <= 0) break;
    s.covered = true;
    budget -= segLength(s);
  }
  for (const k of ['shaded', 'shortest']) {
    const s = d.routes[k].summary;
    s.sun_m = Math.round(s.sun_m * 0.25);
    s.sun_pct = +(100 * s.sun_m / s.distance_m).toFixed(1);
    s.heat_load = 0;
  }
}

function clamp(v, lo, hi) { return Math.min(hi, Math.max(lo, v)); }

/* ------------------------------------------------------------ the fetch -- */

let mockDoc = null;
async function loadMock() {
  if (!mockDoc) mockDoc = await getJSON('mock.json', 8000);
  return mockDoc;
}

async function fetchRoute() {
  const seq = ++state.reqSeq;
  showStatus('Routing…');

  const qs = new URLSearchParams({
    from_lat: state.from.lat, from_lon: state.from.lon,
    to_lat: state.to.lat, to_lon: state.to.lon,
    hour: state.hour, mode: state.mode, compare: 'true'
  });

  let doc = null, mocked = false;

  if (USE_MOCK !== true) {
    try {
      doc = await getJSON(API_BASE + '/route?' + qs.toString());
    } catch (err) {
      if (USE_MOCK === false) {
        if (seq === state.reqSeq) showStatus('API unreachable — ' + err.message, true);
        return;
      }
    }
  }
  if (!doc) {
    try {
      doc = mockForHour(await loadMock(), state.hour, state.mode);
      mocked = true;
    } catch (err) {
      if (seq === state.reqSeq) showStatus('No route data: ' + err.message, true);
      return;
    }
  }

  if (seq !== state.reqSeq) return;         // a newer request already won
  state.data = doc;
  setMocked(mocked);
  render(doc);
  hideStatus();
}

/* ============================================================= RENDER == */

function segmentsToFC(segments, predicate) {
  return {
    type: 'FeatureCollection',
    features: (segments || []).filter(predicate).map((s) => ({
      type: 'Feature',
      properties: { indoor: !!s.indoor, covered: !!s.covered, shade: s.shade },
      geometry: { type: 'LineString', coordinates: s.coords }
    }))
  };
}

function routeToFC(route) {
  if (route.segments && route.segments.length) {
    return segmentsToFC(route.segments, () => true);
  }
  return { type: 'FeatureCollection', features: [route.geojson] };
}

function render(doc) {
  const shaded = doc.routes.shaded;
  const shortest = doc.routes.shortest;

  if (map.getSource('route-short')) {
    map.getSource('route-short').setData(routeToFC(shortest));
    map.getSource('route-shade').setData(
      segmentsToFC(shaded.segments, (s) => !s.indoor && !s.covered));
    map.getSource('route-covered').setData(
      segmentsToFC(shaded.segments, (s) => s.covered && !s.indoor));
    map.getSource('route-indoor').setData(
      segmentsToFC(shaded.segments, (s) => s.indoor));
  }

  renderDelta(shaded.summary, shortest.summary, shaded, shortest);
  renderWeather(doc.weather, doc.hour);
  maybeFit(shaded, shortest);
}

/* Frame the pair of routes, but only when the endpoints actually moved —
   re-framing on every hour tick would be nauseating. */
let lastFitKey = null;
function maybeFit(a, b) {
  const key = [state.from.lat, state.from.lon, state.to.lat, state.to.lon].join(',');
  if (key === lastFitKey) return;
  lastFitKey = key;

  const bounds = new maplibregl.LngLatBounds();
  let n = 0;
  [a, b].forEach((r) => (r.segments || []).forEach((s) =>
    s.coords.forEach((c) => { bounds.extend(c); n++; })));
  if (!n) {
    bounds.extend([state.from.lon, state.from.lat]);
    bounds.extend([state.to.lon, state.to.lat]);
  }
  map.fitBounds(bounds, {
    padding: { top: 90, bottom: 150, left: 90, right: 230 },
    duration: 900, maxZoom: 16.5
  });
}

function renderDelta(a, b, shadedRoute, shortRoute) {
  // a = shaded (ours), b = shortest
  const winter = state.mode === 'winter';

  const aExp = exposedMetres(shadedRoute) || (a.distance_m - a.indoor_m);
  const bExp = exposedMetres(shortRoute) || (b.distance_m - b.indoor_m);

  const dDist = b.distance_m ? ((a.distance_m - b.distance_m) / b.distance_m) * 100 : 0;
  const winA = winter ? aExp : a.sun_m;
  const winB = winter ? bExp : b.sun_m;
  const dWin = winB ? ((winA - winB) / winB) * 100 : 0;

  $('hlCost').textContent = pct(dDist);
  $('hlCostLabel').textContent = Math.abs(dDist) < 0.5 ? 'same length'
    : (dDist > 0 ? 'longer' : 'shorter');
  $('hlWin').textContent = pct(dWin);
  $('hlWinLabel').textContent = winter
    ? (dWin <= 0 ? 'less exposure' : 'more exposure')
    : (dWin <= 0 ? 'less sun' : 'more sun');
  $('headline').classList.toggle('is-flat', dWin > -5);

  $('mShortDist').textContent = fmtM(b.distance_m);
  $('mShadeDist').textContent = fmtM(a.distance_m);

  const key = document.querySelector('.cmp-row-key .cmp-metric');
  if (winter) {
    key.textContent = 'Exposed to rain';
    $('mShortSun').textContent = fmtM(bExp);
    $('mShadeSun').textContent = fmtM(aExp);
  } else {
    key.textContent = 'In direct sun';
    $('mShortSun').textContent = fmtM(b.sun_m);
    $('mShadeSun').textContent = fmtM(a.sun_m);
  }

  const aCov = a.indoor_m + coveredMetres(shadedRoute);
  const bCov = b.indoor_m + coveredMetres(shortRoute);
  $('mShortIndoor').textContent = fmtM(bCov);
  $('mShadeIndoor').textContent = fmtM(aCov);

  $('mShortTime').textContent = b.minutes.toFixed(0) + ' min';
  $('mShadeTime').textContent = a.minutes.toFixed(0) + ' min';
  $('mShortHeat').textContent = Math.round(b.heat_load);
  $('mShadeHeat').textContent = Math.round(a.heat_load);

  const pa = winter ? (100 * aExp / a.distance_m) : a.sun_pct;
  const pb = winter ? (100 * bExp / b.distance_m) : b.sun_pct;
  $('barShort').style.width = clamp(pb, 0, 100).toFixed(1) + '%';
  $('barShade').style.width = clamp(pa, 0, 100).toFixed(1) + '%';
  $('barShortVal').textContent = pb.toFixed(0) + '%';
  $('barShadeVal').textContent = pa.toFixed(0) + '%';
  document.querySelector('.sunbars-cap').textContent = winter
    ? 'share of the walk exposed to rain and wind'
    : 'share of the walk in direct sun';

  $('deltaHour').textContent = pad(state.hour) + ':00 · ' +
    (winter ? 'demo day 22 Aug' : 'demo day 14 Jan');
}

function renderWeather(w, hour) {
  const winter = state.mode === 'winter';

  $('wxTemp').innerHTML = w.apparent_temperature.toFixed(1) + '<em>°C</em>';
  $('wxCloud').innerHTML = Math.round(w.cloud_cover) + '<em>%</em>';

  if (winter) {
    $('wxThirdLabel').textContent = 'Rain / wind';
    $('wxThird').innerHTML = w.precipitation.toFixed(1) +
      '<em>mm</em> <span style="opacity:.45">/</span> ' +
      Math.round(w.wind_speed) + '<em>km/h</em>';
  } else {
    $('wxThirdLabel').textContent = 'UV index';
    $('wxThird').innerHTML = w.uv_index.toFixed(1);
  }

  const df = clamp(w.direct_fraction || 0, 0, 1);
  $('splitDirect').style.width = (df * 100).toFixed(1) + '%';
  $('splitDiffuse').style.width = (100 - df * 100).toFixed(1) + '%';
  $('shadeValue').textContent = Math.round(df * 100) + '%';
  $('wxDirect').textContent = Math.round(w.direct_radiation);
  $('wxDiffuse').textContent = Math.round(w.diffuse_radiation);

  $('splitWhy').innerHTML = df >= 0.5
    ? 'Clear beam dominates — <b>shade is worth taking a detour for</b>. ' +
      'A shadow blocks the direct component almost entirely.'
    : 'Diffuse sky dominates — <b>shade is nearly worthless right now</b>. ' +
      'A purely geometric shade map draws the same shadow anyway and is confidently wrong.';

  $('wHeat').textContent = (w.w_heat != null ? w.w_heat : 0).toFixed(2);
  $('wWet').textContent = (w.w_wet != null ? w.w_wet : 0).toFixed(2);
  $('wxSource').textContent = w.source || '';
  $('sunNote').textContent = 'shadow model · ' + (winter ? '22 Aug' : '14 Jan');
}

/* ============================================================== CHROME == */

function showStatus(text, isError) {
  const el = $('status');
  el.hidden = false;
  el.classList.toggle('is-error', !!isError);
  $('statusText').textContent = text;
}
function hideStatus() { $('status').hidden = true; }

function setMocked(m) {
  if (state.mocked === m) return;
  state.mocked = m;
  const b = $('dataBadge');
  b.hidden = false;
  b.classList.toggle('badge-mock', m);
  b.classList.toggle('badge-live', !m);
  $('dataBadgeText').textContent = m ? 'demo data' : 'live api';
  b.title = m ? 'Backend unreachable — serving web/mock.json'
              : 'Connected to the routing API';
}

function optionLabel(p) {
  return p.name === 'Dropped pin' || p.name === 'Custom point'
    ? 'Dropped pin (' + p.lat.toFixed(4) + ', ' + p.lon.toFixed(4) + ')'
    : p.name;
}

function fillSelect(sel, places, current) {
  sel.innerHTML = '';
  places.forEach((p, i) => {
    const o = document.createElement('option');
    o.value = String(i);
    o.textContent = p.name;
    sel.appendChild(o);
  });
  const custom = document.createElement('option');
  custom.value = 'custom';
  custom.textContent = optionLabel(current);
  custom.hidden = true;
  sel.appendChild(custom);

  const idx = places.findIndex((p) => p.name === current.name &&
    Math.abs(p.lat - current.lat) < 1e-6 && Math.abs(p.lon - current.lon) < 1e-6);
  if (idx >= 0) { sel.value = String(idx); custom.hidden = true; }
  else { custom.hidden = false; sel.value = 'custom'; }
}

function refreshSelects() {
  fillSelect($('fromSel'), state.places, state.from);
  fillSelect($('toSel'), state.places, state.to);
}

function setPoint(which, place) {
  state[which] = place;
  (which === 'from' ? markerA : markerB).setLngLat([place.lon, place.lat]);
  refreshSelects();
  schedule();
}

let debounceT = null;
function schedule() {
  clearTimeout(debounceT);
  debounceT = setTimeout(fetchRoute, DEBOUNCE_MS);
}

function buildTicks() {
  const el = $('ticks');
  el.innerHTML = '';
  HOURS.forEach((h) => {
    if (h % 2 !== 0) { el.appendChild(document.createElement('span')); return; }
    const s = document.createElement('span');
    s.textContent = pad(h);
    s.dataset.hour = h;
    el.appendChild(s);
  });
}
function markTick() {
  $('ticks').querySelectorAll('span').forEach((s) => {
    s.classList.toggle('on', +s.dataset.hour === state.hour);
  });
}

function armPick(which) {
  state.arm = state.arm === which ? null : which;
  $('pinFrom').classList.toggle('is-armed', state.arm === 'from');
  $('pinTo').classList.toggle('is-armed', state.arm === 'to');
  $('pickHint').hidden = !state.arm;
  map.getCanvas().style.cursor = state.arm ? 'crosshair' : '';
}

/* -------------------------------------------------------------- wiring -- */

function wire() {
  document.querySelectorAll('.seg-btn').forEach((btn) => {
    btn.addEventListener('click', () => {
      if (btn.dataset.mode === state.mode) return;
      state.mode = btn.dataset.mode;
      document.body.dataset.mode = state.mode;
      document.querySelectorAll('.seg-btn').forEach((b) => {
        const on = b === btn;
        b.classList.toggle('is-active', on);
        b.setAttribute('aria-selected', String(on));
      });
      schedule();
    });
  });

  $('fromSel').addEventListener('change', (e) => {
    if (e.target.value === 'custom') return;
    setPoint('from', { ...state.places[+e.target.value] });
  });
  $('toSel').addEventListener('change', (e) => {
    if (e.target.value === 'custom') return;
    setPoint('to', { ...state.places[+e.target.value] });
  });

  $('swapBtn').addEventListener('click', () => {
    const a = state.from, b = state.to;
    state.from = b; state.to = a;
    markerA.setLngLat([state.from.lon, state.from.lat]);
    markerB.setLngLat([state.to.lon, state.to.lat]);
    refreshSelects();
    schedule();
  });

  $('pinFrom').addEventListener('click', () => armPick('from'));
  $('pinTo').addEventListener('click', () => armPick('to'));

  const hs = $('hourSlider');
  hs.addEventListener('input', () => {
    state.hour = +hs.value;
    $('clock').textContent = pad(state.hour) + ':00';
    markTick();
    updateShadeFrame();     // instant, frames are preloaded
    schedule();
  });

  const os = $('opacitySlider');
  os.addEventListener('input', () => {
    if (map.getLayer('shade-layer')) {
      map.setPaintProperty('shade-layer', 'raster-opacity', +os.value / 100);
    }
  });

  map.on('click', (e) => {
    if (!state.arm) return;
    setPoint(state.arm, { name: 'Dropped pin', lat: +e.lngLat.lat.toFixed(5), lon: +e.lngLat.lng.toFixed(5) });
    armPick(null);
  });

  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && state.arm) armPick(null);
  });
}

/* ================================================================ BOOT == */

async function loadPlaces() {
  try {
    const p = await getJSON(API_BASE + '/places', 2500);
    if (Array.isArray(p) && p.length) {
      state.places = p.filter((x) => x && x.name && x.lat != null && x.lon != null);
    }
  } catch (e) { /* keep the fallback list */ }
  // Keep the contract demo route as the default if it exists in the list.
  const find = (n) => state.places.find((p) => p.name.toLowerCase().includes(n));
  state.from = { ...(find('melbourne central') || state.places[0]) };
  state.to = { ...(find('federation') || state.places[1] || state.places[0]) };
  markerA.setLngLat([state.from.lon, state.from.lat]);
  markerB.setLngLat([state.to.lon, state.to.lat]);
  refreshSelects();
}

async function boot() {
  document.body.dataset.mode = state.mode;
  buildTicks();
  markTick();
  $('clock').textContent = pad(state.hour) + ':00';
  wire();
  refreshSelects();

  await new Promise((res) => map.on('load', res));

  addRouteLayers();
  markerA.addTo(map);
  markerB.addTo(map);
  setInterval(animateDash, 70);

  state.shade = await resolveShadeSource();
  if (state.shade) {
    preloadFrames(state.shade);
    addShadeLayer();
  } else {
    $('opacitySlider').disabled = true;
    document.querySelector('.opacity label').textContent = 'Shadow n/a';
  }

  await loadPlaces();
  await fetchRoute();
}

map.on('error', (e) => {
  // Tile hiccups are noisy and non-fatal; keep them out of the console.
  const msg = (e && e.error && e.error.message) || '';
  if (/Failed to fetch|NetworkError|403|404/.test(msg)) return;
  console.warn('map:', msg || e);
});

boot();
