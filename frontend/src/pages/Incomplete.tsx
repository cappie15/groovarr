import { useState } from 'react';
import { getSettings } from '../api/settings';
import { ApiError } from '../api/client';
import { type MediaAssetOut, listMediaAssets } from '../api/media';
import { searchAgain } from '../api/search';
import { DataTable, type DataTableColumn } from '../components/DataTable';
import { ErrorBanner } from '../components/ErrorBanner';
import { useToast } from '../components/Toast';
import { useApiQuery } from '../hooks/useApiQuery';

const PAGE_SIZE = 50;

function errorMessage(err: unknown): string {
  return err instanceof ApiError ? err.message : 'Unexpected error — please try again.';
}

function describeOutcome(outcome: { media_state: string }): string {
  switch (outcome.media_state) {
    case 'candidate_selected':
      return 'A better candidate was found — queued for download.';
    case 'incomplete':
      return 'Still nothing better than the current visualizer-class result.';
    default:
      return `Search finished (${outcome.media_state}).`;
  }
}

function SearchAgainButton({ asset, onDone }: { asset: MediaAssetOut; onDone: () => void }) {
  const toast = useToast();
  const [busy, setBusy] = useState(false);

  if (asset.track_id == null) return null;

  async function run() {
    setBusy(true);
    try {
      const outcome = await searchAgain(asset.track_id as number);
      toast.showInfo(`${asset.track_artist ?? 'Track'}: ${describeOutcome(outcome)}`);
      onDone();
    } catch (err) {
      toast.showError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <button type="button" className="btn btn--small btn--primary" disabled={busy} onClick={run}>
      {busy ? 'Searching…' : 'Search Again'}
    </button>
  );
}

/** §36: read-only here on purpose — the toggle itself (with its full
 * warning copy and the ability to change it) belongs on the Settings page,
 * a later pass. This just tells the user, honestly, whether these rows will
 * ever be revisited automatically or need a manual "Search Again" every
 * time. */
function MonitoringStatusNote() {
  const { data } = useApiQuery((signal) => getSettings({ signal }));
  if (!data) return null;

  return data.monitor_better_versions_enabled ? (
    <div className="subtle-note" style={{ marginBottom: 16 }}>
      "Monitor for Better Versions" is <strong>enabled</strong> — these will also be periodically
      re-searched automatically, not just when you click Search Again.
    </div>
  ) : (
    <div className="subtle-note" style={{ marginBottom: 16 }}>
      "Monitor for Better Versions" is <strong>disabled</strong> — these visualizer-class results stay
      as-is unless you click Search Again yourself. Enable it in Settings for periodic automatic
      re-checks (note: it can replace a video with a materially different one, not just a higher-
      resolution copy).
    </div>
  );
}

export default function Incomplete() {
  const [offset, setOffset] = useState(0);

  const { data, loading, error, refetch } = useApiQuery(
    (signal) => listMediaAssets({ state: 'incomplete', limit: PAGE_SIZE, offset }, { signal }),
    [offset],
  );

  const columns: DataTableColumn<MediaAssetOut>[] = [
    {
      key: 'track',
      header: 'Track',
      render: (a) => (
        <div>
          <div style={{ fontWeight: 600 }}>{a.track_artist ?? 'Unknown artist'}</div>
          <div className="subtle-note">
            {a.track_title ?? 'Unknown title'}
            {a.track_release_year ? ` (${a.track_release_year})` : ''}
          </div>
        </div>
      ),
    },
    {
      key: 'file',
      header: 'Current file',
      render: (a) => (
        <span className="subtle-note">
          {a.container ? a.container.toUpperCase() : '—'} · {a.resolution_label ?? 'Unknown quality'}
        </span>
      ),
    },
    {
      key: 'actions',
      header: '',
      align: 'right',
      render: (a) => <SearchAgainButton asset={a} onDone={refetch} />,
    },
  ];

  return (
    <section>
      <div className="page-toolbar">
        <h1>Incomplete / Upgrade Wanted</h1>
      </div>

      <MonitoringStatusNote />

      {error && <ErrorBanner message={`Failed to load incomplete media: ${error}`} onRetry={refetch} />}

      <DataTable
        columns={columns}
        rows={data ?? []}
        getRowKey={(a) => a.id}
        loading={loading}
        emptyMessage="Nothing incomplete right now — no visualizer-class results awaiting a better match."
        pagination={{
          limit: PAGE_SIZE,
          offset,
          hasMore: (data?.length ?? 0) === PAGE_SIZE,
          onOffsetChange: setOffset,
        }}
      />
    </section>
  );
}
