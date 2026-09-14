/**
 * Sidebar navigation structure. Grouped the way Sonarr/Radarr group their
 * left nav — by operational concern, not alphabetically — per the
 * information-first UI principles in docs/00-research-and-architecture-
 * review.md §83.
 */

export type IconName =
  | 'dashboard'
  | 'playlist'
  | 'track'
  | 'wanted'
  | 'incomplete'
  | 'search'
  | 'search-manual'
  | 'activity'
  | 'history'
  | 'settings'
  | 'connect'
  | 'system';

export interface NavItem {
  label: string;
  path: string;
  icon: IconName;
  /** One-line description shown on the item's own placeholder page. */
  description: string;
}

export interface NavGroup {
  label: string;
  items: NavItem[];
}

export const navGroups: NavGroup[] = [
  {
    label: 'Overview',
    items: [
      {
        label: 'Dashboard',
        path: '/',
        icon: 'dashboard',
        description:
          'At-a-glance library health: playlist sync status, counts of wanted/incomplete/missing items, recent activity, and any attention-needed alerts.',
      },
    ],
  },
  {
    label: 'Library',
    items: [
      {
        label: 'Playlists',
        path: '/playlists',
        icon: 'playlist',
        description:
          'Connected Spotify playlists — sync status, track counts, per-playlist Jellyfin/Plex destination settings, and finalize/disconnect controls.',
      },
      {
        label: 'Tracks',
        path: '/tracks',
        icon: 'track',
        description:
          'Every track known to Groovarr across all playlists, with its current media state, canonical metadata, and which playlists reference it.',
      },
    ],
  },
  {
    label: 'Monitoring',
    items: [
      {
        label: 'Wanted / Missing',
        path: '/wanted',
        icon: 'wanted',
        description:
          'Tracks with no acquired media yet — awaiting search, queued, or failed — the queue of work Groovarr still needs to do.',
      },
      {
        label: 'Incomplete / Upgrade Wanted',
        path: '/incomplete',
        icon: 'incomplete',
        description:
          'Tracks with only a lower-confidence or visualizer-class file acquired, or explicitly flagged for a better-version search.',
      },
    ],
  },
  {
    label: 'Search',
    items: [
      {
        label: 'Automatic Search',
        path: '/search/automatic',
        icon: 'search',
        description:
          'Run and review automatic candidate discovery and scoring for wanted tracks, with the deterministic score breakdown behind each decision.',
      },
      {
        label: 'Manual Search',
        path: '/search/manual',
        icon: 'search-manual',
        description:
          'Search YouTube directly for a specific track and hand-pick the candidate to acquire, bypassing automatic selection.',
      },
    ],
  },
  {
    label: 'Operations',
    items: [
      {
        label: 'Activity / Queue',
        path: '/activity',
        icon: 'activity',
        description:
          'In-progress and pending jobs — downloads, remuxing, tagging, server sync — with live status and the ability to cancel or reprioritize.',
      },
      {
        label: 'History',
        path: '/history',
        icon: 'history',
        description:
          'A complete, filterable log of past acquisitions, failures, replacements, and deletions, each with its reason and originating job.',
      },
    ],
  },
  {
    label: 'Configuration',
    items: [
      {
        label: 'Settings',
        path: '/settings',
        icon: 'settings',
        description:
          'Spotify authorization, Jellyfin/Plex server details, naming templates, container/quality policy, and search/acquisition behavior.',
      },
      {
        label: 'Connect',
        path: '/connect',
        icon: 'connect',
        description:
          'Named Jellyfin/Plex notification connections, each subscribed to the specific library events that should trigger it — Sonarr/Radarr-style, replacing an always-on implicit refresh with explicit, per-connection control.',
      },
      {
        label: 'System / Status',
        path: '/system',
        icon: 'system',
        description:
          'Runtime health, pinned yt-dlp/FFmpeg versions and update status, disk usage, and diagnostic logs for the Groovarr container itself.',
      },
    ],
  },
];

export const allNavItems: NavItem[] = navGroups.flatMap((group) => group.items);
