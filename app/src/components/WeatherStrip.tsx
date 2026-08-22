"use client";

/**
 * Conditions, and the direct-vs-diffuse radiation split.
 *
 * The split is the differentiator: shade only blocks the *direct* beam. Under
 * cloud, diffuse light arrives from the whole sky dome and a shadow is worth
 * almost nothing — while a purely geometric shade map draws the same shadow
 * either way. So the split gets a labelled bar and a plain-English readout,
 * not a buried number.
 */

import AnimatedNumber from "./AnimatedNumber";
import { clamp } from "@/lib/metrics";
import type { Mode, Weather } from "@/lib/types";

export interface WeatherStripProps {
  weather: Weather | null;
  mode: Mode;
}

const ZERO: Weather = {
  apparent_temperature: 0, direct_radiation: 0, diffuse_radiation: 0,
  cloud_cover: 0, uv_index: 0, precipitation: 0, wind_speed: 0,
  direct_fraction: 0, w_heat: 0, w_wet: 0, source: "",
};

export default function WeatherStrip({ weather, mode }: WeatherStripProps) {
  const w = weather ?? ZERO;
  const winter = mode === "winter";
  const df = clamp(w.direct_fraction ?? 0, 0, 1);
  const shadeWorthIt = df >= 0.5;

  return (
    <section className="lw-panel lw-weather">
      <header className="lw-panel-head">
        <h2>Conditions</h2>
        <span className="lw-panel-note lw-mono">{w.source || "—"}</span>
      </header>

      <div className="lw-wx-tiles">
        <Tile label="Feels like">
          <AnimatedNumber value={w.apparent_temperature} format={(v) => v.toFixed(1)} />
          <em>°C</em>
        </Tile>
        <Tile label="Cloud">
          <AnimatedNumber value={w.cloud_cover} format={(v) => String(Math.round(v))} />
          <em>%</em>
        </Tile>
        {winter ? (
          <Tile label="Rain / wind">
            <AnimatedNumber value={w.precipitation} format={(v) => v.toFixed(1)} />
            <em>mm</em>
            <span className="lw-wx-sep">/</span>
            <AnimatedNumber value={w.wind_speed} format={(v) => String(Math.round(v))} />
            <em>km/h</em>
          </Tile>
        ) : (
          <Tile label="UV index">
            <AnimatedNumber value={w.uv_index} format={(v) => v.toFixed(1)} />
          </Tile>
        )}
      </div>

      <div className="lw-split">
        <div className="lw-split-head">
          <span className="lw-split-title">Radiation split</span>
          <span className="lw-split-readout">
            shade value
            <AnimatedNumber
              className="lw-split-value"
              value={df * 100}
              format={(v) => `${Math.round(v)}%`}
            />
          </span>
        </div>

        <div
          className="lw-split-bar"
          role="img"
          aria-label={`Direct beam ${Math.round(df * 100)} percent of incoming radiation`}
        >
          <span className="lw-split-direct" style={{ width: `${(df * 100).toFixed(1)}%` }} />
          <span className="lw-split-diffuse" style={{ width: `${(100 - df * 100).toFixed(1)}%` }} />
        </div>

        <div className="lw-split-legend">
          <span>
            <i className="lw-swatch lw-swatch-direct" />
            Direct beam
            <AnimatedNumber className="lw-split-num" value={w.direct_radiation} format={(v) => String(Math.round(v))} />
            <span className="lw-unit">W/m²</span>
          </span>
          <span>
            <i className="lw-swatch lw-swatch-diffuse" />
            Diffuse sky
            <AnimatedNumber className="lw-split-num" value={w.diffuse_radiation} format={(v) => String(Math.round(v))} />
            <span className="lw-unit">W/m²</span>
          </span>
        </div>

        <p className={`lw-split-why${shadeWorthIt ? " is-hot" : ""}`}>
          {shadeWorthIt ? (
            <>
              Clear beam dominates — <b>shade is worth a detour</b>. A shadow blocks the
              direct component almost entirely.
            </>
          ) : (
            <>
              Diffuse sky dominates — <b>shade is nearly worthless right now</b>. A purely
              geometric shade map draws the same shadow anyway, and is confidently wrong.
            </>
          )}
        </p>
      </div>

      <div className="lw-weights">
        <span>
          W<sub>heat</sub>
          <AnimatedNumber className="lw-weight-num" value={w.w_heat ?? 0} format={(v) => v.toFixed(2)} />
        </span>
        <span>
          W<sub>wet</sub>
          <AnimatedNumber className="lw-weight-num" value={w.w_wet ?? 0} format={(v) => v.toFixed(2)} />
        </span>
        <span className="lw-weights-note">cost-function weights</span>
      </div>
    </section>
  );
}

function Tile({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="lw-wx-tile">
      <span className="lw-wx-label">{label}</span>
      <span className="lw-wx-value">{children}</span>
    </div>
  );
}
