import { getHealth } from '../api/client';
import { getSettings, getYoutubeQuotaStatus, getYtdlpVersionStatus } from '../api/settings';
import { ErrorBanner } from '../components/ErrorBanner';
import { StatCard } from '../components/StatCard';
import { StatusBadge } from '../components/StatusBadge';
import { useApiQuery } from '../hooks/useApiQuery';

export default function System() {
  const health = useApiQuery((signal) => getHealth({ signal }));
  const settings = useApiQuery((signal) => getSettings({ signal }));
  const youtubeQuota = useApiQuery((signal) => getYoutubeQuotaStatus({ signal }));
  const ytdlpVersion = useApiQuery((signal) => getYtdlpVersionStatus({ signal }));

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

      <h2 style={{ fontSize: 14, marginBottom: 10 }}>System insights</h2>
      <div className="stat-card-grid">
        <StatCard
          label="YouTube API Quota"
          value={
            youtubeQuota.loading
              ? '—'
              : youtubeQuota.error
                ? '—'
                : `${youtubeQuota.data!.search_calls_today} / ~${youtubeQuota.data!.estimated_daily_search_limit} searches`
          }
          tone={
            youtubeQuota.loading || youtubeQuota.error
              ? 'neutral'
              : quotaTone(youtubeQuota.data!.search_calls_today, youtubeQuota.data!.estimated_daily_search_limit)
          }
          sub={
            youtubeQuota.error
              ? 'Failed to load'
              : !youtubeQuota.loading
                ? `${youtubeQuota.data!.quota_units_used_today} / ${youtubeQuota.data!.quota_units_default_daily} quota units used today (estimate — Groovarr's own call count, not read from Google)`
                : undefined
          }
        />
        <StatCard
          label="yt-dlp Version"
          value={ytdlpVersion.loading || ytdlpVersion.error ? '—' : ytdlpVersion.data!.installed_version}
          tone={
            ytdlpVersion.loading || ytdlpVersion.error
              ? 'neutral'
              : ytdlpVersion.data!.update_available
                ? 'warning'
                : ytdlpVersion.data!.latest_version
                  ? 'success'
                  : 'neutral'
          }
          sub={
            ytdlpVersion.error
              ? 'Failed to load'
              : ytdlpVersion.loading
                ? undefined
                : ytdlpVersion.data!.update_available
                  ? `Update available: ${ytdlpVersion.data!.latest_version}`
                  : ytdlpVersion.data!.latest_version
                    ? 'Up to date'
                    : `Latest version unknown${ytdlpVersion.data!.check_error ? ' — GitHub check failed' : ''}`
          }
        />
      </div>

      <p style={{ color: 'var(--text-muted)', fontSize: 12.5 }}>
        FFmpeg's pinned version is not yet exposed here — yt-dlp's is, above, checked against the latest GitHub
        release (cached for 24h). Both are purely informational: Groovarr never updates either dependency itself.
      </p>
    </section>
  );
}

function quotaTone(callsToday: number, estimatedLimit: number): 'neutral' | 'info' | 'warning' | 'danger' {
  if (callsToday === 0 || estimatedLimit <= 0) return 'neutral';
  const fraction = callsToday / estimatedLimit;
  if (fraction >= 1) return 'danger';
  if (fraction >= 0.8) return 'warning';
  return 'info';
}
