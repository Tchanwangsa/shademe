"use client";

/**
 * The headline trade: "+18% longer -> −77% less sun" at the peak of the day.
 *
 * This is the pitch in one line, so it is the largest thing on screen after
 * the map, and every figure animates when the inputs change.
 *
 * When the router declines to detour, the panel does NOT show a row of zeros.
 * It shows the verdict as a result — the engine considered the detour and
 * correctly turned it down — and names which of the two reasons applied.
 */

import AnimatedNumber from "./AnimatedNumber";
import { ArrowIcon } from "./Icons";
import {
  clamp,
  coveredMetres,
  exposedMetres,
  fmtMetres,
  fmtSignedPct,
  pad2,
  verdictFor,
} from "@/lib/metrics";
import type { Mode, Route, RouteResponse } from "@/lib/types";

export interface DeltaPanelProps {
  data: RouteResponse | null;
  mode: Mode;
  hour: number;
}

const exposedPct = (r: Route) =>
  r.summary.distance_m ? (100 * exposedMetres(r)) / r.summary.distance_m : 0;

export default function DeltaPanel({ data, mode, hour }: DeltaPanelProps) {
  const winter = mode === "winter";
  const shaded = data?.routes.shaded ?? null;
  const shortest = data?.routes.shortest ?? null;

  const a = shaded?.summary;
  const b = shortest?.summary;

  // In winter the metric that matters is exposure to rain and wind: everything
  // neither indoors nor under cover.
  const aExposed = shaded ? exposedMetres(shaded) : 0;
  const bExposed = shortest ? exposedMetres(shortest) : 0;

  const dDist = a && b && b.distance_m ? ((a.distance_m - b.distance_m) / b.distance_m) * 100 : 0;
  const winA = winter ? aExposed : (a?.sun_m ?? 0);
  const winB = winter ? bExposed : (b?.sun_m ?? 0);
  const dWin = winB ? ((winA - winB) / winB) * 100 : 0;

  const verdict = data ? verdictFor(data, mode, exposedPct) : { kind: "detour" as const };
  const declined = verdict.kind === "no-detour";

  const keyMetricLabel = winter ? "Exposed to rain" : "In direct sun";
  const aKey = winter ? aExposed : (a?.sun_m ?? 0);
  const bKey = winter ? bExposed : (b?.sun_m ?? 0);

  const aCover = (a?.indoor_m ?? 0) + (shaded ? coveredMetres(shaded) : 0);
  const bCover = (b?.indoor_m ?? 0) + (shortest ? coveredMetres(shortest) : 0);

  const pctA = a?.distance_m ? clamp((100 * aKey) / a.distance_m, 0, 100) : 0;
  const pctB = b?.distance_m ? clamp((100 * bKey) / b.distance_m, 0, 100) : 0;

  return (
    <section className="lw-panel lw-delta">
      <header className="lw-panel-head">
        <h2>{declined ? "The verdict" : "The trade"}</h2>
        <span className="lw-panel-note">
          {pad2(hour)}:00 AEDT · {winter ? "winter" : "summer"}
        </span>
      </header>

      {declined ? (
        <NoDetour
          reason={verdict.reason}
          goodPct={verdict.shortestGoodPct}
          winter={winter}
          wHeat={data?.weather.w_heat ?? 0}
          wWet={data?.weather.w_wet ?? 0}
        />
      ) : (
        <div className="lw-headline">
          <span className="lw-hl-part">
            <AnimatedNumber className="lw-hl-cost" value={dDist} format={fmtSignedPct} />
            <span className="lw-hl-cost-label">
              {Math.abs(dDist) < 0.5 ? "same length" : dDist > 0 ? "longer" : "shorter"}
            </span>
          </span>
          <ArrowIcon className="lw-hl-arrow" />
          <span className="lw-hl-part">
            <AnimatedNumber className="lw-hl-win" value={dWin} format={fmtSignedPct} />
            <span className="lw-hl-win-label">
              {winter
                ? dWin <= 0
                  ? "less exposure"
                  : "more exposure"
                : dWin <= 0
                  ? "less sun"
                  : "more sun"}
            </span>
          </span>
        </div>
      )}

      <div className="lw-cmp">
        <div className="lw-cmp-row lw-cmp-head">
          <span className="lw-cmp-metric" />
          <span className="lw-cmp-col">
            <i className="lw-swatch lw-swatch-short" />
            Shortest
            <em>what maps give you</em>
          </span>
          <span className="lw-cmp-col lw-cmp-col-shade">
            <i className="lw-swatch lw-swatch-shade" />
            Laneway
            <em>{declined ? "same route" : "weather-weighted"}</em>
          </span>
        </div>

        <Row label="Distance">
          <AnimatedNumber className="lw-num" value={b?.distance_m ?? 0} format={fmtMetres} />
          <AnimatedNumber className="lw-num is-accent" value={a?.distance_m ?? 0} format={fmtMetres} />
        </Row>

        <div className="lw-cmp-row is-key">
          <span className="lw-cmp-metric">{keyMetricLabel}</span>
          <AnimatedNumber className="lw-num" value={bKey} format={fmtMetres} />
          <AnimatedNumber className="lw-num is-accent" value={aKey} format={fmtMetres} />
        </div>

        <Row label="Indoor / covered">
          <AnimatedNumber className="lw-num" value={bCover} format={fmtMetres} />
          <AnimatedNumber className="lw-num is-accent" value={aCover} format={fmtMetres} />
        </Row>

        <Row label="Walk time">
          <AnimatedNumber className="lw-num" value={b?.minutes ?? 0} format={(v) => `${v.toFixed(0)} min`} />
          <AnimatedNumber className="lw-num is-accent" value={a?.minutes ?? 0} format={(v) => `${v.toFixed(0)} min`} />
        </Row>

        <Row label="Heat load">
          <AnimatedNumber className="lw-num" value={b?.heat_load ?? 0} format={(v) => String(Math.round(v))} />
          <AnimatedNumber className="lw-num is-accent" value={a?.heat_load ?? 0} format={(v) => String(Math.round(v))} />
        </Row>
      </div>

      <div className="lw-sunbars">
        <Bar label="Shortest" pct={pctB} tone="short" />
        <Bar label="Laneway" pct={pctA} tone="shade" />
        <p className="lw-sunbars-cap">
          {winter
            ? "share of the walk exposed to rain and wind"
            : "share of the walk in direct sun"}
        </p>
      </div>
    </section>
  );
}

/* --------------------------------------------------- declined-detour state -- */

function NoDetour({
  reason,
  goodPct,
  winter,
  wHeat,
  wWet,
}: {
  reason: "low-value" | "already-good";
  goodPct: number;
  winter: boolean;
  wHeat: number;
  wWet: number;
}) {
  const lowValue = reason === "low-value";
  const weight = winter ? wWet : wHeat;

  return (
    <div className="lw-verdict">
      <div className="lw-verdict-mark" aria-hidden="true">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2}
          strokeLinecap="round" strokeLinejoin="round">
          <circle cx="12" cy="12" r="9.2" opacity=".35" />
          <path d="M7.8 12.2 10.7 15 16.4 9.2" />
        </svg>
      </div>

      <div className="lw-verdict-body">
        <p className="lw-verdict-head">No detour worth taking.</p>
        <p className="lw-verdict-why">
          {lowValue ? (
            <>
              The weighting is effectively off (W<sub>{winter ? "wet" : "heat"}</sub>{" "}
              <b>{weight.toFixed(2)}</b>).{" "}
              {winter
                ? "It is neither wet nor windy enough for cover to be worth extra metres."
                : "It is not hot enough, and the light is too diffuse for a shadow to buy anything."}
            </>
          ) : (
            <>
              Still weighted hard (W<sub>{winter ? "wet" : "heat"}</sub>{" "}
              <b>{weight.toFixed(2)}</b>), but the direct route is already{" "}
              <b>{Math.round(goodPct)}% {winter ? "sheltered" : "shaded"}</b> at this hour.
              There is nothing left to buy.
            </>
          )}
        </p>
        <p className="lw-verdict-foot">
          The router considered the detour and declined it. Walk the direct route.
        </p>
      </div>
    </div>
  );
}

/* --------------------------------------------------------------- pieces -- */

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="lw-cmp-row">
      <span className="lw-cmp-metric">{label}</span>
      {children}
    </div>
  );
}

function Bar({ label, pct, tone }: { label: string; pct: number; tone: "short" | "shade" }) {
  return (
    <div className="lw-sunbar">
      <span className="lw-sunbar-label">{label}</span>
      <span className="lw-sunbar-track">
        <span
          className={`lw-sunbar-fill lw-sunbar-fill-${tone}`}
          style={{ width: `${pct.toFixed(1)}%` }}
        />
      </span>
      <AnimatedNumber
        className="lw-sunbar-val"
        value={pct}
        format={(v) => `${v.toFixed(0)}%`}
      />
    </div>
  );
}
