"use client";

/**
 * The single client component that owns Laneway's state.
 *
 * Everything below it is presentational. The map lives in `MapView` and is
 * created once; this component only ever hands it new data.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import ControlPanel from "./ControlPanel";
import DeltaPanel from "./DeltaPanel";
import HourSlider from "./HourSlider";
import MapView from "./MapView";
import WeatherStrip from "./WeatherStrip";
import { BrandMark } from "./Icons";

import {
  FALLBACK_PLACES,
  fetchPlaces,
  fetchRoute,
  preloadShadeFrames,
  resolveShadeSource,
} from "@/lib/api";
import { exposedMetres, verdictFor } from "@/lib/metrics";
import {
  DEFAULT_HOUR,
  type Mode,
  type Place,
  type Route,
  type RouteResponse,
  type ShadeSource,
} from "@/lib/types";

type PickTarget = "from" | "to";

/** One settled request, tagged with the query that produced it. */
interface Settled {
  key: string;
  data: RouteResponse;
  isMock: boolean;
}

const DEBOUNCE_MS = 200;

const exposedPct = (r: Route) =>
  r.summary.distance_m ? (100 * exposedMetres(r)) / r.summary.distance_m : 0;

function findPlace(list: Place[], needle: string, fallback: Place): Place {
  return list.find((p) => p.name.toLowerCase().includes(needle)) ?? fallback;
}

export default function LanewayApp() {
  const [mode, setMode] = useState<Mode>("summer");
  const [hour, setHour] = useState(DEFAULT_HOUR);
  const [places, setPlaces] = useState<Place[]>(FALLBACK_PLACES);
  const [from, setFrom] = useState<Place>(FALLBACK_PLACES[0]);
  const [to, setTo] = useState<Place>(FALLBACK_PLACES[1]);
  const [arm, setArm] = useState<PickTarget | null>(null);
  const [shadeOpacity, setShadeOpacity] = useState(82);

  const [settled, setSettled] = useState<Settled | null>(null);
  const [shade, setShade] = useState<ShadeSource | null>(null);
  const [shadeChecked, setShadeChecked] = useState(false);

  const seqRef = useRef(0);
  const framesRef = useRef<HTMLImageElement[]>([]);

  // The query currently on screen. Request status is derived by comparing it
  // with the key of the last settled response — no status state to keep in sync.
  const requestKey = `${from.lat},${from.lon},${to.lat},${to.lon},${hour},${mode}`;
  const data = settled?.data ?? null;
  const isMock = settled?.isMock ?? false;
  const status: "boot" | "routing" | "idle" =
    settled === null ? "boot" : settled.key !== requestKey ? "routing" : "idle";

  /* -------------------------------------------------------------- boot -- */
  useEffect(() => {
    let cancelled = false;

    (async () => {
      const [list, src] = await Promise.all([fetchPlaces(), resolveShadeSource()]);
      if (cancelled) return;

      setPlaces(list);
      setFrom((cur) => findPlace(list, "melbourne central", list[0] ?? cur));
      setTo((cur) => findPlace(list, "federation", list[1] ?? cur));

      if (src) {
        // Warm all 15 frames so scrubbing never hits the network.
        framesRef.current = preloadShadeFrames(src);
        setShade(src);
      }
      setShadeChecked(true);
    })();

    return () => {
      cancelled = true;
    };
  }, []);

  /* ------------------------------------------ debounced route recompute -- */
  useEffect(() => {
    const seq = ++seqRef.current;

    const timer = setTimeout(async () => {
      const result = await fetchRoute({ from, to, hour, mode });
      if (seq !== seqRef.current) return; // a newer request already won
      setSettled({ key: requestKey, data: result.data, isMock: result.isMock });
    }, DEBOUNCE_MS);

    return () => clearTimeout(timer);
  }, [from, to, hour, mode, requestKey]);

  /* ----------------------------------------------------------- handlers -- */
  const handleSelect = useCallback((which: PickTarget, place: Place) => {
    (which === "from" ? setFrom : setTo)(place);
  }, []);

  const handlePick = useCallback((which: PickTarget, lat: number, lon: number) => {
    (which === "from" ? setFrom : setTo)({ name: "Dropped pin", lat, lon });
    setArm(null);
  }, []);

  const handleArm = useCallback((which: PickTarget) => {
    setArm((cur) => (cur === which ? null : which));
  }, []);

  const handleSwap = useCallback(() => {
    setFrom(to);
    setTo(from);
  }, [from, to]);

  useEffect(() => {
    if (!arm) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setArm(null);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [arm]);

  /* ------------------------------------------------------------- advice -- */
  const { advice, declined } = useMemo(() => {
    if (!data) return { advice: "Computing…", declined: false };

    const v = verdictFor(data, mode, exposedPct);
    if (v.kind === "no-detour") {
      return {
        declined: true,
        advice:
          v.reason === "low-value"
            ? mode === "winter"
              ? "Mild and dry — no detour worth taking"
              : "Shade buys nothing here — walk direct"
            : `Direct route already ${Math.round(v.shortestGoodPct)}% ${
                mode === "winter" ? "sheltered" : "shaded"
              }`,
      };
    }

    const a = data.routes.shaded.summary;
    const b = data.routes.shortest.summary;
    const key = mode === "winter" ? exposedPct : (r: Route) => r.summary.sun_pct;
    const winA = key(data.routes.shaded);
    const winB = key(data.routes.shortest);
    const gain = winB ? ((winA - winB) / winB) * 100 : 0;
    const indoor = Math.round(a.indoor_pct);

    if (gain <= -50) {
      return {
        declined: false,
        advice: `Strong detour — ${indoor}% of it indoors`,
      };
    }
    if (gain <= -10) {
      return { declined: false, advice: "Worth a short detour" };
    }
    return {
      declined: false,
      advice: `Marginal — ${Math.round(b.sun_pct)}% sun either way`,
    };
  }, [data, mode]);

  const shadeAvailable = shade !== null;
  const booting = status === "boot" && !data;

  return (
    <div className="lw-app" data-mode={mode}>
      {/* ============================ LEFT RAIL ============================ */}
      <aside className="lw-rail">
        <header className="lw-brand">
          <BrandMark className="lw-brand-mark" />
          <div className="lw-brand-text">
            <h1>Laneway</h1>
            <p>Weather-aware walking · Melbourne CBD</p>
          </div>
          <DataBadge isMock={isMock} ready={status !== "boot"} />
        </header>

        <p className="lw-thesis">
          Melbourne has a second pedestrian network — arcades, subways, building
          pass-throughs — that mapping apps ignore. We route you through it, weighted
          by the weather.
        </p>

        <ControlPanel
          mode={mode}
          onModeChange={setMode}
          places={places}
          from={from}
          to={to}
          onSelect={handleSelect}
          onSwap={handleSwap}
          arm={arm}
          onArm={handleArm}
        />

        {booting ? <SkeletonPanels /> : <DeltaPanel data={data} mode={mode} hour={hour} />}

        <footer className="lw-rail-foot">
          <span>Buildings + canopy: City of Melbourne Open Data (CC-BY)</span>
          <span>Network: OpenStreetMap (ODbL) · Weather: Open-Meteo (CC-BY)</span>
        </footer>
      </aside>

      {/* ============================== MAP =============================== */}
      <main className="lw-map-wrap">
        <MapView
          route={data}
          shade={shade}
          hour={hour}
          shadeOpacity={shadeOpacity}
          from={from}
          to={to}
          arm={arm}
          onPick={handlePick}
        />

        {/* The radiation split sits on the map, beside the shadow field it
            explains — it is the differentiator, not a footnote. */}
        {!booting && (
          <div className="lw-wx-float">
            <WeatherStrip weather={data?.weather ?? null} mode={mode} />
          </div>
        )}

        <div className="lw-map-top">
          <div className="lw-glass lw-legend">
            <span className="lw-legend-item">
              <i className="lw-line lw-line-short" />
              Shortest route
            </span>
            <span className="lw-legend-item">
              <i className="lw-line lw-line-shade" />
              Laneway route
            </span>
            <span className="lw-legend-item">
              <i className="lw-line lw-line-indoor" />
              Indoor / arcade
            </span>
            <span className="lw-legend-item">
              <i className="lw-line lw-line-covered" />
              Covered / awning
            </span>
            <span className="lw-legend-item">
              <i className="lw-chip" />
              Modelled shadow
            </span>
          </div>
        </div>

        {status !== "idle" && !arm && (
          <div className="lw-glass lw-status">
            <span className="lw-spinner" />
            <span>{status === "boot" ? "Loading shadow model…" : "Routing…"}</span>
          </div>
        )}

        {arm && (
          <div className="lw-glass lw-pickbar">
            Click the map to set <b>{arm === "from" ? "the origin" : "the destination"}</b>
            <span className="lw-esc">Esc to cancel</span>
          </div>
        )}

        <HourSlider
          hour={hour}
          onHourChange={setHour}
          opacity={shadeOpacity}
          onOpacityChange={setShadeOpacity}
          mode={mode}
          shadeAvailable={shadeAvailable}
          advice={advice}
          declined={declined}
        />

        {shadeChecked && !shadeAvailable && (
          <div className="lw-glass lw-shade-warn">
            Shadow frames unavailable — routing metrics are unaffected.
          </div>
        )}
      </main>
    </div>
  );
}

/* ------------------------------------------------------------- fragments -- */

function DataBadge({ isMock, ready }: { isMock: boolean; ready: boolean }) {
  if (!ready) return null;
  return (
    <span
      className={`lw-badge ${isMock ? "is-mock" : "is-live"}`}
      title={
        isMock
          ? "Routing API unreachable — serving a bundled capture of demo day 2026-01-26"
          : "Connected to the live routing API"
      }
    >
      <span className="lw-badge-dot" />
      {isMock ? "demo data" : "live api"}
    </span>
  );
}

function SkeletonPanels() {
  return (
    <>
      <section className="lw-panel lw-skeleton" aria-hidden="true">
        <span className="lw-sk lw-sk-head" />
        <span className="lw-sk lw-sk-hero" />
        <span className="lw-sk lw-sk-row" />
        <span className="lw-sk lw-sk-row" />
        <span className="lw-sk lw-sk-row" />
      </section>
      <section className="lw-panel lw-skeleton" aria-hidden="true">
        <span className="lw-sk lw-sk-head" />
        <span className="lw-sk lw-sk-tiles" />
        <span className="lw-sk lw-sk-row" />
      </section>
    </>
  );
}
