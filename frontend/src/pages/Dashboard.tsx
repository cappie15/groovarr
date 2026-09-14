import { getDashboardSummary } from '../api/dashboard';
import { DataTable, type DataTableColumn } from '../components/DataTable';
import { ErrorBanner } from '../components/ErrorBanner';
import { StatCard } from '../components/StatCard';
import { StatusBadge } from '../components/StatusBadge';
import { useApiQuery } from '../hooks/useApiQuery';

function formatDateTime(iso: string | null): string {
  if (!iso) return '—';
  return new Date(iso).toLocaleString();
}

interface StateRow {
  state: string;
  count: number;
}

const STATE_COLUMNS: DataTableColumn<StateRow>[] = [
  { key: 'state', header: 'State', render: (r) => <StatusBadge status={r.state} /> },
  { key: 'count', header: 'Count', align: 'right', render: (r) => r.count, sortAccessor: (r) => r.count },
];

export default function Dashboard() {
  const { data, loading, error, refetch } = useApiQuery((signal) => getDashboardSummary({ signal }));

  if (error) {
    return (
      <section>
        <h1>Dashboard</h1>
        <ErrorBanner message={`Failed to load dashboard summary: ${error}`} onRetry={refetch} />
      </section>
    );
  }

  const stateRows: StateRow[] = data
    ? Object.entries(data.media_by_state).map(([state, count]) => ({ state, count }))
    : [];

  return (
    <section>
      <h1>Dashboard</h1>

      <div className="stat-card-grid">
        <StatCard label="Connected Playlists" value={loading ? '—' : data!.connected_playlists} />
        <StatCard label="Monitored Tracks" value={loading ? '—' : data!.monitored_tracks} />
        <StatCard label="Active Downloads" value={loading ? '—' : data!.active_queue_count} />
        <StatCard
          label="Manual Review Required"
          value={loading ? '—' : data!.manual_review_required_count}
          tone={!loading && data!.manual_review_required_count > 0 ? 'warning' : 'neutral'}
        />
        <StatCard
          label="Download Failures"
          value={loading ? '—' : data!.download_failed_count}
          tone={!loading && data!.download_failed_count > 0 ? 'danger' : 'neutral'}
        />
        <StatCard label="Pending Jellyfin Sync" value={loading ? '—' : data!.pending_jellyfin_sync} />
        <StatCard label="Pending Plex Sync" value={loading ? '—' : data!.pending_plex_sync} />
        <StatCard label="Finalized Playlists" value={loading ? '—' : data!.finalized_playlists} />
      </div>

      <div style={{ display: 'flex', gap: 24, flexWrap: 'wrap', marginBottom: 24 }}>
        <StatCard label="Last Spotify Sync" value={loading ? '—' : formatDateTime(data!.last_spotify_sync_at)} />
        <StatCard label="Next Spotify Sync" value={loading ? '—' : formatDateTime(data!.next_spotify_sync_at)} />
      </div>

      <h2 style={{ fontSize: 14, marginBottom: 10 }}>Media by State</h2>
      <DataTable
        columns={STATE_COLUMNS}
        rows={stateRows}
        getRowKey={(r) => r.state}
        loading={loading}
        emptyMessage="No media tracked yet — connect a playlist to get started."
      />
    </section>
  );
}
