import { getHealth } from '../api/client';
import { getSettings } from '../api/settings';
import { ErrorBanner } from '../components/ErrorBanner';
import { StatCard } from '../components/StatCard';
import { StatusBadge } from '../components/StatusBadge';
import { useApiQuery } from '../hooks/useApiQuery';

export default function System() {
  const health = useApiQuery((signal) => getHealth({ signal }));
  const settings = useApiQuery((signal) => getSettings({ signal }));

  const error = health.error ?? settings.error;
  if (error) {
    return (
      <section>
        <h1>System / Status</h1>
        <ErrorBanner
          message={`Failed to load system status: ${error}`}
          onRetry={() => {
            health.refetch();
            settings.refetch();
          }}
        />
      </section>
    );
  }

  const loading = health.loading || settings.loading;

  return (
    <section>
      <h1>System / Status</h1>

      <div className="stat-card-grid">
        <StatCard
          label="Backend"
          value={loading ? '—' : <StatusBadge status={health.data!.status === 'ok' ? 'available' : 'download_failed'} label={health.data!.status} />}
        />
        <StatCard
          label="Database"
          value={loading ? '—' : <StatusBadge status={health.data!.db === 'ok' ? 'available' : 'download_failed'} label={health.data!.db} />}
        />
        <StatCard label="Version" value={loading ? '—' : health.data!.version} />
      </div>

      <h2 style={{ fontSize: 14, marginBottom: 10 }}>Integrations</h2>
      <div className="stat-card-grid">
        <StatCard
          label="Spotify"
          value={
            loading ? (
              '—'
            ) : (
              <StatusBadge
                status={settings.data!.spotify_client_configured ? 'synced' : 'not_configured'}
                label={settings.data!.spotify_client_configured ? 'Configured' : 'Not Configured'}
              />
            )
          }
          sub={!loading && settings.data!.spotify_needs_reauth ? 'Needs re-authorization' : undefined}
        />
        <StatCard
          label="Jellyfin"
          value={
            loading ? (
              '—'
            ) : (
              <StatusBadge
                status={settings.data!.jellyfin_configured ? 'synced' : 'not_configured'}
                label={settings.data!.jellyfin_configured ? 'Configured' : 'Not Configured'}
              />
            )
          }
        />
        <StatCard
          label="Plex"
          value={
            loading ? (
              '—'
            ) : (
              <StatusBadge
                status={settings.data!.plex_configured ? 'synced' : 'not_configured'}
                label={settings.data!.plex_configured ? 'Configured' : 'Not Configured'}
              />
            )
          }
        />
      </div>

      <p style={{ color: 'var(--text-muted)', fontSize: 12.5 }}>
        Pinned yt-dlp/FFmpeg dependency versions are not yet exposed by the backend's <code>/health</code>{' '}
        endpoint — this is a known gap for a future pass (architecture doc §88: "Expose their versions in
        System/Status"), not something this page can show real data for yet.
      </p>
    </section>
  );
}
