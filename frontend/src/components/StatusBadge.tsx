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

export interface StatusBadgeProps {
  status: string;
  /** Override the auto-derived label (rarely needed). */
  label?: string;
}

export function StatusBadge({ status, label }: StatusBadgeProps) {
  const tone = TONE_BY_STATE[status] ?? 'neutral';
  return <span className={`status-badge status-badge--${tone}`}>{label ?? humanize(status)}</span>;
}
