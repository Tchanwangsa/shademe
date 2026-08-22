"use client";

/** Season toggle, origin/destination pickers, and map-pick arming. */

import type { Mode, Place } from "@/lib/types";
import { ChevronIcon, PinIcon, RainIcon, SunIcon, SwapIcon } from "./Icons";

type PickTarget = "from" | "to";

export interface ControlPanelProps {
  mode: Mode;
  onModeChange: (m: Mode) => void;
  places: Place[];
  from: Place;
  to: Place;
  onSelect: (which: PickTarget, place: Place) => void;
  onSwap: () => void;
  arm: PickTarget | null;
  onArm: (which: PickTarget) => void;
}

const CUSTOM = "__custom__";

function sameSpot(a: Place, b: Place) {
  return Math.abs(a.lat - b.lat) < 1e-6 && Math.abs(a.lon - b.lon) < 1e-6;
}

function EndpointField({
  label,
  places,
  current,
  which,
  armed,
  onSelect,
  onArm,
}: {
  label: string;
  places: Place[];
  current: Place;
  which: PickTarget;
  armed: boolean;
  onSelect: (which: PickTarget, place: Place) => void;
  onArm: (which: PickTarget) => void;
}) {
  const idx = places.findIndex((p) => sameSpot(p, current));
  const value = idx >= 0 ? String(idx) : CUSTOM;

  return (
    <div className="lw-field">
      <span className="lw-field-label">{label}</span>
      <div className="lw-field-row">
        <div className="lw-select-wrap">
          <select
            className="lw-select"
            aria-label={label}
            value={value}
            onChange={(e) => {
              if (e.target.value === CUSTOM) return;
              onSelect(which, { ...places[Number(e.target.value)] });
            }}
          >
            {places.map((p, i) => (
              <option key={`${p.name}-${i}`} value={String(i)}>
                {p.name}
              </option>
            ))}
            {idx < 0 && (
              <option value={CUSTOM}>
                {`Dropped pin (${current.lat.toFixed(4)}, ${current.lon.toFixed(4)})`}
              </option>
            )}
          </select>
          <ChevronIcon className="lw-select-chev" />
        </div>
        <button
          type="button"
          className={`lw-pin-btn${armed ? " is-armed" : ""}`}
          aria-pressed={armed}
          title={`Pick ${which === "from" ? "origin" : "destination"} on the map`}
          onClick={() => onArm(which)}
        >
          <PinIcon className="lw-pin-ico" />
        </button>
      </div>
    </div>
  );
}

export default function ControlPanel({
  mode,
  onModeChange,
  places,
  from,
  to,
  onSelect,
  onSwap,
  arm,
  onArm,
}: ControlPanelProps) {
  return (
    <section className="lw-panel lw-controls">
      <div className="lw-seg" role="tablist" aria-label="Season mode">
        <button
          type="button"
          role="tab"
          aria-label="Summer mode, weighted for heat"
          aria-selected={mode === "summer"}
          className={`lw-seg-btn${mode === "summer" ? " is-active" : ""}`}
          onClick={() => onModeChange("summer")}
        >
          <SunIcon className="lw-seg-ico" />
          <span>
            Summer
            <em>heat</em>
          </span>
        </button>
        <button
          type="button"
          role="tab"
          aria-label="Winter mode, weighted for rain and wind"
          aria-selected={mode === "winter"}
          className={`lw-seg-btn${mode === "winter" ? " is-active" : ""}`}
          onClick={() => onModeChange("winter")}
        >
          <RainIcon className="lw-seg-ico" />
          <span>
            Winter
            <em>rain + wind</em>
          </span>
        </button>
      </div>

      <div className="lw-od">
        <div className="lw-od-spine" aria-hidden="true">
          <span className="lw-od-dot lw-od-dot-a" />
          <span className="lw-od-line" />
          <span className="lw-od-dot lw-od-dot-b" />
        </div>

        <div className="lw-od-fields">
          <EndpointField
            label="From"
            places={places}
            current={from}
            which="from"
            armed={arm === "from"}
            onSelect={onSelect}
            onArm={onArm}
          />
          <EndpointField
            label="To"
            places={places}
            current={to}
            which="to"
            armed={arm === "to"}
            onSelect={onSelect}
            onArm={onArm}
          />
        </div>

        <button
          type="button"
          className="lw-swap-btn"
          title="Swap origin and destination"
          aria-label="Swap origin and destination"
          onClick={onSwap}
        >
          <SwapIcon className="lw-swap-ico" />
        </button>
      </div>

      {arm && <p className="lw-hint">Click anywhere on the map to place the point.</p>}
    </section>
  );
}
