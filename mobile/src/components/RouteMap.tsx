import { useMemo, useRef, useEffect } from 'react';
import {
  Map,
  Camera,
  GeoJSONSource,
  Layer,
  type CameraRef,
  type LngLatBounds,
} from '@maplibre/maplibre-react-native';
import type { Place, RouteOption } from '../lib/api';
import { CBD_CENTER, MAP_STYLE } from '../lib/config';
import { exposureOf } from '../lib/format';
import { useTheme } from '../lib/theme';

/** [west, south, east, north], the order LngLatBounds uses. */
function boundsOf(coords: [number, number][]): LngLatBounds {
  const lons = coords.map((c) => c[0]);
  const lats = coords.map((c) => c[1]);
  return [Math.min(...lons), Math.min(...lats), Math.max(...lons), Math.max(...lats)];
}

/** The selected walk, drawn segment by segment in its exposure colour.
 *
 * Colouring the line by what it is actually like to walk is the whole product; a flat
 * polyline would throw away the one thing this engine knows that a normal map does not.
 * Unselected options stay as faint dashed hairlines so the choice is visible without
 * competing with it.
 */
export function RouteMap({
  options,
  selected,
  from,
  to,
  sheetHeight,
}: {
  options: RouteOption[];
  selected: RouteOption | null;
  from: Place | null;
  to: Place | null;
  sheetHeight: number;
}) {
  const theme = useTheme();
  const camera = useRef<CameraRef>(null);

  const others = useMemo(
    () => ({
      type: 'FeatureCollection' as const,
      features: options
        .filter((o) => o.id !== selected?.id)
        .map((o) => ({ type: 'Feature' as const, properties: {}, geometry: o.geometry })),
    }),
    [options, selected],
  );

  const active = useMemo(
    () => ({
      type: 'FeatureCollection' as const,
      features: (selected?.segments ?? []).map((s) => ({
        type: 'Feature' as const,
        properties: { exposure: exposureOf(s) },
        geometry: { type: 'LineString' as const, coordinates: s.coords },
      })),
    }),
    [selected],
  );

  const endpoints = useMemo(() => {
    const pts = [from, to].filter(Boolean) as Place[];
    return {
      type: 'FeatureCollection' as const,
      features: pts.map((p, i) => ({
        type: 'Feature' as const,
        properties: { kind: i === 0 ? 'start' : 'end' },
        geometry: { type: 'Point' as const, coordinates: [p.lon, p.lat] },
      })),
    };
  }, [from, to]);

  useEffect(() => {
    if (!selected) return;
    // Padding is asymmetric on purpose: the sheet covers the bottom of the map, so the
    // route has to be fitted into the strip that is actually visible above it.
    camera.current?.fitBounds(boundsOf(selected.geometry.coordinates), {
      padding: { top: 90, right: 48, bottom: sheetHeight + 40, left: 48 },
      duration: 600,
    });
  }, [selected, sheetHeight]);

  return (
    <Map
      style={{ flex: 1 }}
      mapStyle={theme.isDark ? MAP_STYLE.dark : MAP_STYLE.light}
      logo={false}
      attributionPosition={{ bottom: 8, left: 8 }}
      compass={false}
    >
      <Camera ref={camera} initialViewState={{ center: CBD_CENTER, zoom: 14 }} />

      <GeoJSONSource id="other-routes" data={others}>
        <Layer
          id="other-routes-line"
          type="line"
          layout={{ 'line-cap': 'round', 'line-join': 'round' }}
          paint={{
            'line-color': theme.routeIdle,
            'line-width': 3,
            'line-dasharray': [2, 2],
          }}
        />
      </GeoJSONSource>

      <GeoJSONSource id="active-route" data={active}>
        <Layer
          id="active-route-casing"
          type="line"
          layout={{ 'line-cap': 'round', 'line-join': 'round' }}
          paint={{ 'line-color': theme.isDark ? '#0D0F0C' : '#FFFFFF', 'line-width': 9 }}
        />
        <Layer
          id="active-route-line"
          type="line"
          layout={{ 'line-cap': 'round', 'line-join': 'round' }}
          paint={{
            'line-color': [
              'match',
              ['get', 'exposure'],
              'sun',
              theme.sun,
              'indoor',
              theme.indoor,
              theme.shade,
            ],
            'line-width': 5.5,
          }}
        />
      </GeoJSONSource>

      <GeoJSONSource id="endpoints" data={endpoints}>
        <Layer
          id="endpoints-halo"
          type="circle"
          paint={{
            'circle-radius': 8,
            'circle-color': theme.isDark ? '#0D0F0C' : '#FFFFFF',
          }}
        />
        <Layer
          id="endpoints-dot"
          type="circle"
          paint={{
            'circle-radius': 5,
            'circle-color': ['match', ['get', 'kind'], 'start', theme.indoor, '#D85A30'],
          }}
        />
      </GeoJSONSource>
    </Map>
  );
}
