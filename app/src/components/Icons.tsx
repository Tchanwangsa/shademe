/** Inline SVG icon set. No icon fonts, no emoji. */

type P = { className?: string };

export function BrandMark({ className }: P) {
  return (
    <svg className={className} viewBox="0 0 32 32" aria-hidden="true" fill="none"
      stroke="currentColor" strokeWidth={2.1} strokeLinecap="round">
      <path d="M4 28V13.5C4 8.253 8.253 4 13.5 4S23 8.253 23 13.5V28" />
      <path d="M10 28v-14a3.5 3.5 0 0 1 7 0v14" opacity=".55" />
      <path d="M28 28V9" opacity=".35" />
    </svg>
  );
}

export function SunIcon({ className }: P) {
  return (
    <svg className={className} viewBox="0 0 20 20" aria-hidden="true" fill="none"
      stroke="currentColor" strokeWidth={1.6} strokeLinecap="round">
      <circle cx="10" cy="10" r="3.6" />
      <path d="M10 1.6v2.2M10 16.2v2.2M18.4 10h-2.2M3.8 10H1.6M15.9 4.1l-1.6 1.6M5.7 14.3l-1.6 1.6M15.9 15.9l-1.6-1.6M5.7 5.7 4.1 4.1" />
    </svg>
  );
}

export function RainIcon({ className }: P) {
  return (
    <svg className={className} viewBox="0 0 20 20" aria-hidden="true" fill="none"
      stroke="currentColor" strokeWidth={1.6} strokeLinecap="round" strokeLinejoin="round">
      <path d="M4.2 11.6a3.4 3.4 0 0 1 .7-6.7 4.5 4.5 0 0 1 8.5-.7 3.6 3.6 0 0 1 2 6.9" />
      <path d="M6.6 14.2 5.6 17M10.4 14.2 9.4 17M14.2 14.2 13.2 17" opacity=".65" />
    </svg>
  );
}

export function PinIcon({ className }: P) {
  return (
    <svg className={className} viewBox="0 0 20 20" aria-hidden="true" fill="none"
      stroke="currentColor" strokeWidth={1.55} strokeLinejoin="round">
      <path d="M10 18s6-5.2 6-9.6A6 6 0 0 0 4 8.4C4 12.8 10 18 10 18Z" />
      <circle cx="10" cy="8.3" r="2.1" />
    </svg>
  );
}

export function SwapIcon({ className }: P) {
  return (
    <svg className={className} viewBox="0 0 20 20" aria-hidden="true" fill="none"
      stroke="currentColor" strokeWidth={1.6} strokeLinecap="round" strokeLinejoin="round">
      <path d="M6.5 3.5v13M6.5 16.5 3.6 13.4M6.5 16.5l2.9-3.1M13.5 16.5v-13M13.5 3.5l2.9 3.1M13.5 3.5l-2.9 3.1" />
    </svg>
  );
}

export function ArrowIcon({ className }: P) {
  return (
    <svg className={className} viewBox="0 0 40 24" aria-hidden="true" fill="none"
      stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round">
      <path d="M2 12h33M28 5l7 7-7 7" />
    </svg>
  );
}

export function ChevronIcon({ className }: P) {
  return (
    <svg className={className} viewBox="0 0 16 16" aria-hidden="true" fill="none"
      stroke="currentColor" strokeWidth={1.7} strokeLinecap="round" strokeLinejoin="round">
      <path d="M4 6.5 8 10.5l4-4" />
    </svg>
  );
}
