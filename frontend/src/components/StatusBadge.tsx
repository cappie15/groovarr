import type { ReactNode } from 'react';
import './StatusBadge.css';

/**
 * Visual treatment for the various lifecycle/state enums surfaced across the
 * app (MediaState today; sync-status-style strings later) — Sonarr/Radarr-
 * style status pills, not a generic "chip" component. Accepts a plain
 * string (rather than importing every enum type here) so it stays usable
 * for any future state value without this file needing to know about it;
 * an unrecognized value still renders, just in the neutral "default" tone.
 */

type Tone = 'neutral' | 'info' | 'warning' | 'danger' | 'success';

const TONE_BY_STATE: Record<string, Tone> = {
  // MediaState (backend/app/db/models/media.py)
  wanted: 'neutral',
  searching: 'info',
  manual_review_required: 'warning',
  candidate_selected: 'info',
  queued: 'info',
  downloading: 'info',
  processing: 'info',
  importing: 'info',
  available: 'success',
  incomplete: 'warning',
  download_failed: 'danger',
  missing: 'neutral',
  // SyncStatus (backend/app/db/models/media.py) — per-asset Jellyfin/Plex
  // sync status.
  not_configured: 'neutral',
  pending_sync: 'info',
  synced: 'success',
  failed_sync: 'danger',
  // ExternalPlaylistSyncState (backend/app/db/models/external_playlist.py)
  // — per-playlist Jellyfin/Plex playlist reconstruction status. Distinct
  // value set from SyncStatus above (no "_sync"/"_configured" suffix), so
  // both need their own entries here.
  pending: 'info',
  failed: 'danger',
  needs_resolution: 'warning',
};

const LABEL_OVERRIDES: Record<string, string> = {
  manual_review_required: 'Manual Review',
  candidate_selected: 'Candidate Selected',
  download_failed: 'Download Failed',
  pending_sync: 'Pending Sync',
  failed_sync: 'Failed Sync',
  not_configured: 'Not Configured',
  needs_resolution: 'Needs Resolution',
};

function humanize(value: string): string {
  return (
    LABEL_OVERRIDES[value] ??
    value
      .split('_')
      .map((word) => word.charAt(0).toUpperCase() + word.slice(1))
      .join(' ')
  );
}

// One small glyph per tone (not per state — the tone already carries the
// meaning) rendered ahead of the label, per the "Signal Accents" direction.
// `currentColor` picks up --badge-ink automatically from the pill's own
// text color, so these never need their own color logic.
const TONE_ICON: Record<Tone, ReactNode> = {
  neutral: null,
  info: (
    <svg width="10" height="10" viewBox="0 0 12 12" fill="none" stroke="currentColor" strokeWidth="2">
      <path d="M6 1.5v5.5M3 7l3 3 3-3" />
    </svg>
  ),
  success: (
    <svg width="10" height="10" viewBox="0 0 12 12" fill="none" stroke="currentColor" strokeWidth="2">
      <path d="M2.5 6.3 5 8.7 9.5 3.3" />
    </svg>
  ),
  warning: (
    <svg width="10" height="10" viewBox="0 0 12 12" fill="none" stroke="currentColor" strokeWidth="1.8">
      <path d="M6 1.5a4.5 4.5 0 1 1-3.2 1.3" />
    </svg>
  ),
  danger: (
    <svg width="10" height="10" viewBox="0 0 12 12" fill="none" stroke="currentColor" strokeWidth="2">
      <line x1="3" y1="3" x2="9" y2="9" />
      <line x1="9" y1="3" x2="3" y2="9" />
    </svg>
  ),
};

export interface StatusBadgeProps {
  status: string;
  /** Override the auto-derived label (rarely needed). */
  label?: string;
}

export function StatusBadge({ status, label }: StatusBadgeProps) {
  const tone = TONE_BY_STATE[status] ?? 'neutral';
  return (
    <span className={`status-badge status-badge--${tone}`}>
      {TONE_ICON[tone]}
      {label ?? humanize(status)}
    </span>
  );
}
