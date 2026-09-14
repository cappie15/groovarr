import type { IconName } from '../nav';

/**
 * Tiny hand-rolled 16x16 stroke icons — deliberately not an icon-library
 * dependency, in keeping with "no heavy component library" (architecture
 * doc §83). Each is a single simple glyph, monochrome, inherits `color`.
 */
export function Icon({ name }: { name: IconName }) {
  const common = {
    width: 16,
    height: 16,
    viewBox: '0 0 16 16',
    fill: 'none',
    stroke: 'currentColor',
    strokeWidth: 1.4,
    strokeLinecap: 'round' as const,
    strokeLinejoin: 'round' as const,
    'aria-hidden': true,
  };

  switch (name) {
    case 'dashboard':
      return (
        <svg {...common}>
          <rect x="1.5" y="1.5" width="6" height="6" />
          <rect x="8.5" y="1.5" width="6" height="4" />
          <rect x="8.5" y="7.5" width="6" height="7" />
          <rect x="1.5" y="9.5" width="6" height="5" />
        </svg>
      );
    case 'playlist':
      return (
        <svg {...common}>
          <path d="M1.5 3h9M1.5 8h9M1.5 13h6" />
          <circle cx="13" cy="11" r="2" />
          <path d="M15 11V4l-2 .5" />
        </svg>
      );
    case 'track':
      return (
        <svg {...common}>
          <circle cx="4.5" cy="12" r="2" />
          <path d="M6.5 12V2.5L14 1v9.5" />
          <circle cx="12" cy="10.5" r="2" />
        </svg>
      );
    case 'wanted':
      return (
        <svg {...common}>
          <circle cx="8" cy="8" r="6.5" />
          <path d="M8 4.5v4l2.5 1.5" />
        </svg>
      );
    case 'incomplete':
      return (
        <svg {...common}>
          <path d="M8 1.5a6.5 6.5 0 1 0 6.5 6.5" />
          <path d="M8 1.5v4.5h4.5" />
        </svg>
      );
    case 'search':
      return (
        <svg {...common}>
          <circle cx="7" cy="7" r="5" />
          <path d="M14.5 14.5 11 11" />
        </svg>
      );
    case 'search-manual':
      return (
        <svg {...common}>
          <circle cx="6.5" cy="6.5" r="4.5" />
          <path d="M14 14 10 10" />
          <path d="M4.5 6.5h4M6.5 4.5v4" />
        </svg>
      );
    case 'activity':
      return (
        <svg {...common}>
          <path d="M1.5 8.5h3l2-5 3 9 2-6.5h3" />
        </svg>
      );
    case 'history':
      return (
        <svg {...common}>
          <circle cx="8" cy="8.5" r="6" />
          <path d="M8 5v3.5l2.5 1.5" />
          <path d="M4 1.5 2 4M12 1.5l2 2.5" />
        </svg>
      );
    case 'settings':
      return (
        <svg {...common}>
          <circle cx="8" cy="8" r="2.3" />
          <path d="M8 1.8v1.6M8 12.6v1.6M14.2 8h-1.6M3.4 8H1.8M12.3 3.7l-1.1 1.1M4.8 11.1l-1.1 1.1M12.3 12.3l-1.1-1.1M4.8 4.9 3.7 3.7" />
        </svg>
      );
    case 'connect':
      return (
        <svg {...common}>
          <circle cx="3.2" cy="8" r="1.9" />
          <circle cx="12.8" cy="3.6" r="1.9" />
          <circle cx="12.8" cy="12.4" r="1.9" />
          <path d="M5 7.2l6-2.7M5 8.8l6 2.7" />
        </svg>
      );
    case 'system':
      return (
        <svg {...common}>
          <rect x="2" y="2.5" width="12" height="4" rx="0.5" />
          <rect x="2" y="9.5" width="12" height="4" rx="0.5" />
          <circle cx="4.5" cy="4.5" r="0.6" fill="currentColor" stroke="none" />
          <circle cx="4.5" cy="11.5" r="0.6" fill="currentColor" stroke="none" />
        </svg>
      );
    default:
      return null;
  }
}
