"use client";

import { useAnimatedNumber } from "@/lib/useAnimatedNumber";

/** A number that tweens to its new value instead of snapping. */
export default function AnimatedNumber({
  value,
  format,
  className,
  duration,
}: {
  value: number;
  format: (v: number) => string;
  className?: string;
  duration?: number;
}) {
  const v = useAnimatedNumber(Number.isFinite(value) ? value : 0, duration);
  return <span className={className}>{format(v)}</span>;
}
