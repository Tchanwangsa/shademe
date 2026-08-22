"use client";

/**
 * MapLibre canvas for Laneway.
 *
 * The map instance is created exactly once, inside an effect, from a dynamic
 * import so that nothing touches `window` during SSR. Every subsequent state
 * change updates sources, paint properties and marker positions imperatively —
 * the map is never re-created and never re-rendered by React.
 */

import { useEffect, useRef, useState } from "react";
import type {
  GeoJSONSource,
  ImageSource,
  LngLat,
  Map as MlMap,
  Marker as MlMarker,
} from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";

import type { Place, RouteResponse, Segment, ShadeSource } from "@/lib/types";

type PickTarget = "from" | "to";

export interface MapViewProps {
  route: RouteResponse | null;
  shade: ShadeSource | null;
  hour: number;
  shadeOpacity: number;
  from: Place;
  to: Place;
  arm: PickTarget | null;
  onPick: (which: PickTarget, lat: number, lon: number) => void;
}

const EMPTY_FC = { type: "FeatureCollection" as const, features: [] };

/* ------------------------------------------------------------ geometry -- */

function segmentsToFC(segments: Segment[], keep: (s: Segment) => boolean) {
  return {
    type: "FeatureCollection" as const,
    features: segments.filter(keep).map((s) => ({
      type: "Feature" as const,
      properties: { indoor: s.indoor, covered: s.covered, shade: s.shade },
      geometry: { type: "LineString" as const, coordinates: s.coords },
    })),
  };
}

/** Flowing dash on the indoor legs — reads as "you move through here". */
const DASH_SEQ: number[][] = [
  [0, 4, 3], [0.5, 4, 2.5], [1, 4, 2], [1.5, 4, 1.5], [2, 4, 1], [2.5, 4, 0.5],
  [3, 4, 0], [0, 0.5, 3, 3.5], [0, 1, 3, 3], [0, 1.5, 3, 2.5], [0, 2, 3, 2],
  [0, 2.5, 3, 1.5], [0, 3, 3, 1], [0, 3.5, 3, 0.5],
];

/** Framing that keeps the route clear of the floating chrome: conditions panel
 *  on the left, legend on the right, hour scrubber along the bottom. */
const FIT_OPTIONS = {
  padding: { top: 56, bottom: 214, left: 396, right: 208 },
  maxZoom: 16.4,
} as const;

/* ================================================================ view == */

export default function MapView(props: MapViewProps) {
  const { route, shade, hour, shadeOpacity, from, to, arm } = props;

  const containerRef = useRef<HTMLDivElement | null>(null);
  const mapRef = useRef<MlMap | null>(null);
  const markerARef = useRef<MlMarker | null>(null);
  const markerBRef = useRef<MlMarker | null>(null);
  const fitKeyRef = useRef<string | null>(null);
  const boundsRef = useRef<[[number, number], [number, number]] | null>(null);
  const shadeAddedRef = useRef(false);
  const [ready, setReady] = useState(false);

  // Latest-props ref so long-lived map listeners never close over stale state.
  // Declared first so it is refreshed before any effect below reads it.
  const liveRef = useRef(props);
  useEffect(() => {
    liveRef.current = props;
  });

  /* ------------------------------------------- create the map, once only -- */
  useEffect(() => {
    let cancelled = false;
    let dashTimer: ReturnType<typeof setInterval> | null = null;

    (async () => {
      const gl = await import("maplibre-gl");
      if (cancelled || !containerRef.current) return;

      // maplibre v6 derives its worker URL from `import.meta.url` of its own
      // bundle. Under Turbopack that resolves to a hashed chunk directory where
      // the worker file does not exist, so the worker 404s, GeoJSON sources are
      // never tiled, and no route lines render at all. Point it at the copy in
      // /public instead (kept in sync by scripts/copy-maplibre-worker.mjs).
      gl.setWorkerUrl("/maplibre/maplibre-gl-worker.mjs");

      const map = new gl.Map({
        container: containerRef.current,
        attributionControl: false,
        style: {
          version: 8,
          sources: {
            carto: {
              type: "raster",
              tiles: ["a", "b", "c"].map(
                (s) =>
                  `https://${s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}${
                    typeof window !== "undefined" && window.devicePixelRatio > 1.4 ? "@2x" : ""
                  }.png`,
              ),
              tileSize: 256,
              maxzoom: 20,
              attribution:
                '&copy; <a href="https://carto.com/attributions">CARTO</a> &copy; ' +
                '<a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
            },
          },
          layers: [
            { id: "bg", type: "background", paint: { "background-color": "#070a10" } },
            {
              id: "carto",
              type: "raster",
              source: "carto",
              paint: {
                "raster-opacity": 0.76,
                "raster-saturation": -0.18,
                "raster-contrast": 0.1,
              },
            },
          ],
        },
        center: [144.9656, -37.814],
        zoom: 14.4,
        minZoom: 11,
        maxZoom: 19,
        pitch: 0,
        dragRotate: false,
      });
      mapRef.current = map;

      if (process.env.NODE_ENV === "development") {
        (window as unknown as { __lwMap?: MlMap }).__lwMap = map;
      }

      map.addControl(new gl.AttributionControl({ compact: true }), "bottom-right");
      map.addControl(new gl.NavigationControl({ showCompass: false }), "bottom-left");
      map.touchZoomRotate.disableRotation();

      // Tile hiccups are noisy and non-fatal; keep the console clean for demos.
      map.on("error", (e) => {
        const msg = (e as { error?: { message?: string } })?.error?.message ?? "";
        if (/Failed to fetch|NetworkError|40[34]|abort/i.test(msg)) return;
        console.warn("[map]", msg || e);
      });

      map.on("click", (e) => {
        const live = liveRef.current;
        if (!live.arm) return;
        const p = e.lngLat as LngLat;
        live.onPick(live.arm, +p.lat.toFixed(5), +p.lng.toFixed(5));
      });

      await new Promise<void>((resolve) => {
        if (map.loaded()) resolve();
        else map.once("load", () => resolve());
      });
      if (cancelled) return;

      addRouteLayers(map);

      const pin = (cls: string, label: string) => {
        const el = document.createElement("div");
        el.className = `lw-pin ${cls}`;
        el.textContent = label;
        return el;
      };

      const a = new gl.Marker({ element: pin("lw-pin-a", "A"), draggable: true })
        .setLngLat([liveRef.current.from.lon, liveRef.current.from.lat])
        .addTo(map);
      const b = new gl.Marker({ element: pin("lw-pin-b", "B"), draggable: true })
        .setLngLat([liveRef.current.to.lon, liveRef.current.to.lat])
        .addTo(map);
      markerARef.current = a;
      markerBRef.current = b;

      a.on("dragend", () => {
        const p = a.getLngLat();
        liveRef.current.onPick("from", +p.lat.toFixed(5), +p.lng.toFixed(5));
      });
      b.on("dragend", () => {
        const p = b.getLngLat();
        liveRef.current.onPick("to", +p.lat.toFixed(5), +p.lng.toFixed(5));
      });

      let step = 0;
      dashTimer = setInterval(() => {
        if (!mapRef.current?.getLayer("route-indoor-line")) return;
        mapRef.current.setPaintProperty(
          "route-indoor-line",
          "line-dasharray",
          DASH_SEQ[step],
        );
        step = (step + 1) % DASH_SEQ.length;
      }, 70);

      setReady(true);
    })();

    return () => {
      cancelled = true;
      if (dashTimer) clearInterval(dashTimer);
      markerARef.current = null;
      markerBRef.current = null;
      shadeAddedRef.current = false;
      fitKeyRef.current = null;
      boundsRef.current = null;
      mapRef.current?.remove();
      mapRef.current = null;
      setReady(false);
    };
    // Intentionally empty: the map is created once for the lifetime of the app.
  }, []);

  /* ------------------------------------------------------- shade overlay -- */
  useEffect(() => {
    const map = mapRef.current;
    if (!ready || !map || !shade || shadeAddedRef.current) return;

    map.addSource("shade", {
      type: "image",
      url: shade.frame(liveRef.current.hour),
      coordinates: shade.coordinates,
    });
    map.addLayer(
      {
        id: "shade-layer",
        type: "raster",
        source: "shade",
        paint: {
          "raster-opacity": liveRef.current.shadeOpacity / 100,
          "raster-fade-duration": 0, // instant swap, no cross-fade flicker
          "raster-resampling": "linear",
          // The shipped frames are a low-alpha navy wash and are near-invisible
          // on a dark basemap. Lift and cool them so the shadow field reads.
          "raster-brightness-min": 0.48,
          "raster-brightness-max": 1,
          "raster-saturation": 0.35,
          "raster-contrast": 0.2,
        },
      },
      "route-short-casing",
    );
    shadeAddedRef.current = true;
  }, [ready, shade]);

  /* --------------------------------------------- swap frame on hour change -- */
  useEffect(() => {
    const map = mapRef.current;
    if (!ready || !map || !shade || !shadeAddedRef.current) return;
    const src = map.getSource("shade") as ImageSource | undefined;
    src?.updateImage({ url: shade.frame(hour), coordinates: shade.coordinates });
  }, [ready, shade, hour]);

  /* ----------------------------------------------------- overlay opacity -- */
  useEffect(() => {
    const map = mapRef.current;
    if (!ready || !map?.getLayer("shade-layer")) return;
    map.setPaintProperty("shade-layer", "raster-opacity", shadeOpacity / 100);
  }, [ready, shadeOpacity]);

  /* ---------------------------------------------------------- route data -- */
  useEffect(() => {
    const map = mapRef.current;
    if (!ready || !map || !route) return;

    const shaded = route.routes.shaded;
    const shortest = route.routes.shortest;

    const set = (id: string, data: GeoJSON.FeatureCollection | typeof EMPTY_FC) => {
      const src = map.getSource(id) as GeoJSONSource | undefined;
      src?.setData(data as GeoJSON.FeatureCollection);
    };

    set(
      "route-short",
      shortest.segments.length || !shortest.geojson
        ? segmentsToFC(shortest.segments, () => true)
        : { type: "FeatureCollection", features: [shortest.geojson as GeoJSON.Feature] },
    );
    set("route-shade", segmentsToFC(shaded.segments, (s) => !s.indoor && !s.covered));
    set("route-covered", segmentsToFC(shaded.segments, (s) => s.covered && !s.indoor));
    set("route-indoor", segmentsToFC(shaded.segments, (s) => s.indoor));
  }, [ready, route]);

  /* ------------------------------------------------------------- markers -- */
  useEffect(() => {
    markerARef.current?.setLngLat([from.lon, from.lat]);
  }, [from.lon, from.lat]);

  useEffect(() => {
    markerBRef.current?.setLngLat([to.lon, to.lat]);
  }, [to.lon, to.lat]);

  /* ------------------------------------------------------------- framing -- */
  useEffect(() => {
    const map = mapRef.current;
    if (!ready || !map || !route) return;

    // Re-frame only when the endpoints actually moved. Doing it on every hour
    // tick would be nauseating on video.
    const key = `${from.lat},${from.lon},${to.lat},${to.lon}`;
    if (fitKeyRef.current === key) return;
    fitKeyRef.current = key;

    let minLon = Infinity, minLat = Infinity, maxLon = -Infinity, maxLat = -Infinity;
    const extend = (lon: number, lat: number) => {
      minLon = Math.min(minLon, lon); maxLon = Math.max(maxLon, lon);
      minLat = Math.min(minLat, lat); maxLat = Math.max(maxLat, lat);
    };
    for (const r of [route.routes.shaded, route.routes.shortest]) {
      for (const s of r.segments) for (const c of s.coords) extend(c[0], c[1]);
    }
    if (!Number.isFinite(minLon)) {
      extend(from.lon, from.lat);
      extend(to.lon, to.lat);
    }

    boundsRef.current = [
      [minLon, minLat],
      [maxLon, maxLat],
    ];
    map.fitBounds(boundsRef.current, { ...FIT_OPTIONS, duration: 900 });
  }, [ready, route, from.lat, from.lon, to.lat, to.lon]);

  /* Re-frame when the map is resized. Without this, a window resize leaves the
     route at the zoom that suited the old canvas and it drifts off-centre. */
  useEffect(() => {
    const map = mapRef.current;
    if (!ready || !map) return;

    let timer: ReturnType<typeof setTimeout> | null = null;
    const onResize = () => {
      if (timer) clearTimeout(timer);
      timer = setTimeout(() => {
        const b = boundsRef.current;
        if (b) map.fitBounds(b, { ...FIT_OPTIONS, duration: 0 });
      }, 140);
    };

    map.on("resize", onResize);
    return () => {
      if (timer) clearTimeout(timer);
      map.off("resize", onResize);
    };
  }, [ready]);

  /* -------------------------------------------------------------- cursor -- */
  useEffect(() => {
    const map = mapRef.current;
    if (!ready || !map) return;
    map.getCanvas().style.cursor = arm ? "crosshair" : "";
  }, [ready, arm]);

  return <div ref={containerRef} className="lw-map" aria-label="Route map" />;
}

/* --------------------------------------------------------- layer set-up -- */

function addRouteLayers(map: MlMap) {
  for (const id of ["route-short", "route-shade", "route-covered", "route-indoor"]) {
    map.addSource(id, { type: "geojson", data: EMPTY_FC });
  }

  // -- shortest: what a mapping app gives you. Muted, recessive, and offset so
  //    that where the two routes coincide you can still see both ribbons.
  map.addLayer({
    id: "route-short-casing",
    type: "line",
    source: "route-short",
    layout: { "line-cap": "round", "line-join": "round" },
    paint: {
      "line-color": "#05080d",
      "line-width": 10,
      "line-opacity": 0.6,
      "line-blur": 1,
      "line-offset": 4,
    },
  });
  map.addLayer({
    id: "route-short-line",
    type: "line",
    source: "route-short",
    layout: { "line-cap": "round", "line-join": "round" },
    paint: {
      "line-color": "#98a5b8",
      "line-width": 4.5,
      "line-opacity": 0.92,
      "line-offset": 4,
    },
  });

  // -- our route, outdoor legs
  map.addLayer({
    id: "route-shade-glow",
    type: "line",
    source: "route-shade",
    layout: { "line-cap": "round", "line-join": "round" },
    paint: {
      "line-color": "#5ef2c0",
      "line-width": 18,
      "line-opacity": 0.2,
      "line-blur": 12,
    },
  });
  map.addLayer({
    id: "route-shade-line",
    type: "line",
    source: "route-shade",
    layout: { "line-cap": "round", "line-join": "round" },
    paint: { "line-color": "#5ef2c0", "line-width": 5.5 },
  });

  // -- covered: awning, verandah, colonnade
  map.addLayer({
    id: "route-covered-line",
    type: "line",
    source: "route-covered",
    layout: { "line-cap": "butt", "line-join": "round" },
    paint: {
      "line-color": "#9fe6c8",
      "line-width": 5,
      "line-dasharray": [3, 1.6],
      "line-opacity": 0.95,
    },
  });

  // -- indoor: arcades, subways, building pass-throughs. The money shot.
  map.addLayer({
    id: "route-indoor-glow",
    type: "line",
    source: "route-indoor",
    layout: { "line-cap": "round", "line-join": "round" },
    paint: {
      "line-color": "#ffc861",
      "line-width": 24,
      "line-opacity": 0.32,
      "line-blur": 14,
    },
  });
  map.addLayer({
    id: "route-indoor-halo",
    type: "line",
    source: "route-indoor",
    layout: { "line-cap": "round", "line-join": "round" },
    paint: {
      "line-color": "#ffc861",
      "line-width": 9,
      "line-opacity": 0.24,
      "line-blur": 3,
    },
  });
  map.addLayer({
    id: "route-indoor-line",
    type: "line",
    source: "route-indoor",
    layout: { "line-cap": "butt", "line-join": "round" },
    paint: { "line-color": "#ffd98a", "line-width": 6, "line-dasharray": [1.6, 1.1] },
  });
}
