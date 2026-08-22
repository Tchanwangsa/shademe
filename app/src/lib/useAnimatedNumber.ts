"use client";

import { useEffect, useRef, useState } from "react";

const easeOutCubic = (t: number) => 1 - Math.pow(1 - t, 3);

/**
 * Tween a number toward `target` with requestAnimationFrame.
 *
 * Used for every headline figure so a control change reads as the numbers
 * moving rather than snapping. Respects `prefers-reduced-motion`.
 *
 * `target` must be finite — callers coerce before passing it in.
 */
export function useAnimatedNumber(target: number, duration = 620): number {
  const [value, setValue] = useState(target);
  const fromRef = useRef(target);
  const rafRef = useRef<number | null>(null);

  useEffect(() => {
    const from = fromRef.current;
    if (from === target) return;

    const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    if (reduced) {
      const id = requestAnimationFrame(() => {
        fromRef.current = target;
        setValue(target);
      });
      return () => cancelAnimationFrame(id);
    }

    const start = performance.now();
    const tick = (now: number) => {
      const t = Math.min(1, (now - start) / duration);
      const v = from + (target - from) * easeOutCubic(t);
      fromRef.current = t < 1 ? v : target;
      setValue(fromRef.current);
      if (t < 1) rafRef.current = requestAnimationFrame(tick);
    };
    rafRef.current = requestAnimationFrame(tick);

    return () => {
      if (rafRef.current !== null) cancelAnimationFrame(rafRef.current);
    };
  }, [target, duration]);

  return value;
}
