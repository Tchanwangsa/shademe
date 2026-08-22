"use client";

/**
 * The hero control. Scrubbing 09 -> 14 -> 18 *is* the demo: it moves the
 * shadow field, the radiation split, and the routing advice all at once, so it
 * gets the largest single control on screen and instant, preloaded frames.
 */

import { pad2 } from "@/lib/metrics";
import { HOURS, type Mode } from "@/lib/types";

export interface HourSliderProps {
  hour: number;
  onHourChange: (h: number) => void;
  opacity: number;
  onOpacityChange: (v: number) => void;
  mode: Mode;
  shadeAvailable: boolean;
  /** Short line describing what the router decided at this hour. */
  advice: string;
  /** True when the router declined to detour — tints the readout. */
  declined: boolean;
}

const FIRST = HOURS[0];
const LAST = HOURS[HOURS.length - 1];

export default function HourSlider({
  hour,
  onHourChange,
  opacity,
  onOpacityChange,
  mode,
  shadeAvailable,
  advice,
  declined,
}: HourSliderProps) {
  const progress = ((hour - FIRST) / (LAST - FIRST)) * 100;

  return (
    <div className="lw-map-bottom">
      <div className="lw-glass lw-scrub">
        <div className="lw-scrub-head">
          <div className="lw-scrub-clock-group">
            <span className="lw-scrub-clock">{pad2(hour)}:00</span>
            <span className="lw-scrub-tz">AEDT · Melbourne</span>
          </div>
          <div className="lw-scrub-meta">
            <span className="lw-scrub-title">Time of day</span>
            <span className={`lw-scrub-advice${declined ? " is-declined" : ""}`}>
              {advice}
            </span>
          </div>
        </div>

        <div className="lw-scrub-track">
          <input
            type="range"
            className="lw-hour-input"
            min={FIRST}
            max={LAST}
            step={1}
            value={hour}
            aria-label="Hour of day, Melbourne local time"
            aria-valuetext={`${pad2(hour)}:00`}
            style={{ ["--lw-progress" as string]: `${progress}%` }}
            onChange={(e) => onHourChange(Number(e.target.value))}
          />
        </div>

        <div className="lw-scrub-ticks" aria-hidden="true">
          {HOURS.map((h) => (
            <span key={h} className={h === hour ? "is-on" : undefined}>
              {h % 2 === 0 ? pad2(h) : "·"}
            </span>
          ))}
        </div>
      </div>

      <div className="lw-glass lw-opacity">
        <label htmlFor="lw-opacity-input">
          {shadeAvailable ? "Shadow layer" : "Shadow n/a"}
        </label>
        <input
          id="lw-opacity-input"
          type="range"
          min={0}
          max={100}
          step={1}
          value={opacity}
          disabled={!shadeAvailable}
          aria-label="Shadow overlay opacity"
          style={{ ["--lw-progress" as string]: `${opacity}%` }}
          onChange={(e) => onOpacityChange(Number(e.target.value))}
        />
        <span className="lw-opacity-val">
          {shadeAvailable ? `${opacity}%` : "—"} · {mode === "winter" ? "winter" : "summer"} sun
        </span>
      </div>
    </div>
  );
}
