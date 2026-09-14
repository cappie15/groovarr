import { useId } from 'react';

/**
 * The Groovarr mark: a teal→violet disc with a play-cut — reads as both a
 * record groove and a play affordance. Deliberately a distinct hue pair
 * (teal/violet) from the semantic state palette (info/success/warning/
 * danger) so it's never mistaken for a status color. `useId()` keeps the
 * gradient's `id` collision-free if this ever renders more than once on a
 * page (it currently only does, in the sidebar, but this makes it safe by
 * construction rather than by convention).
 */
export function BrandMark({ size = 22 }: { size?: number }) {
  const gradientId = useId();
  return (
    <svg width={size} height={size} viewBox="0 0 32 32" aria-hidden="true">
      <defs>
        <linearGradient id={gradientId} x1="4" y1="4" x2="28" y2="28" gradientUnits="userSpaceOnUse">
          <stop offset="0" stopColor="#22d3c5" />
          <stop offset="1" stopColor="#6e56cf" />
        </linearGradient>
      </defs>
      <circle cx="16" cy="16" r="15" fill={`url(#${gradientId})`} />
      <circle cx="16" cy="16" r="10.5" fill="none" stroke="rgba(255,255,255,.35)" strokeWidth="1.4" />
      <path d="M13.2 10.6 L22 16 L13.2 21.4 Z" fill="#10151b" />
    </svg>
  );
}
