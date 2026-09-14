import { useEffect, useState } from 'react';
import { ApiError } from '../api/client';
import { listMediaAssets, type MediaAssetOut, type MediaState } from '../api/media';
import { cancelQueued, listQueue, retryDownload, type QueueItemOut } from '../api/queue';
import { DataTable, type DataTableColumn } from '../components/DataTable';
import { ErrorBanner } from '../components/ErrorBanner';
import { StatusBadge } from '../components/StatusBadge';
import { useToast } from '../components/Toast';
import { useApiQuery } from '../hooks/useApiQuery';

const PAGE_SIZE = 50;

// The queue only ever contains assets in one of these states (mirrors the
// backend's own `_LISTED_STATES` in app/api/queue.py — the active states
// plus DOWNLOAD_FAILED, which is listed specifically so Retry has something
// to act on) — fetching each state once (rather than the whole, potentially
// large, media library) is enough to build an id → track-artist/title
// lookup for the rows this page shows.
const LISTED_STATES: MediaState[] = [
  'candidate_selected',
  'queued',
  'downloading',
  'processing',
  'importing',
  'download_failed',
];

function errorMessage(err: unknown): string {
  return err instanceof ApiError ? err.message : 'Unexpected error — please try again.';
}

function trackLabel(asset: MediaAssetOut | undefined, trackId: number | null): string {
  if (asset?.track_artist) return `${asset.track_artist} — ${asset.track_title ?? 'Untitled'}`;
  if (trackId) return `Track #${trackId}`;
  return '—';
}

function latestAttempt(item: QueueItemOut) {
  return item.attempts.length > 0 ? item.attempts[item.attempts.length - 1] : null;
}

function RowActions({ item, onDone }: { item: QueueItemOut; onDone: () => void }) {
  const toast = useToast();
  const [busy, setBusy] = useState(false);

  async function run(action: 'retry' | 'cancel') {
    setBusy(true);
    try {
      await (action === 'retry' ? retryDownload(item.media_asset_id) : cancelQueued(item.media_asset_id));
      toast.showInfo(action === 'retry' ? 'Retry scheduled.' : 'Cancelled.');
      onDone();
    } catch (err) {
      toast.showError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  if (item.state === 'download_failed') {
    return (
      <button type="button" className="btn btn--small btn--primary" disabled={busy} onClick={() => run('retry')}>
        {busy ? 'Retrying…' : 'Retry'}
      </button>
    );
  }
  if (item.state === 'queued') {
    return (
      <button type="button" className="btn btn--small btn--danger" disabled={busy} onClick={() => run('cancel')}>
        {busy ? 'Cancelling…' : 'Cancel'}
      </button>
    );
  }
  return <span className="subtle-note">In progress…</span>;
}

export default function Activity() {
  const [offset, setOffset] = useState(0);
  const [assetsByTrackId, setAssetsByTrackId] = useState<Map<number, MediaAssetOut>>(new Map());

  const {
    data: queue,
    loading,
    error,
    refetch,
  } = useApiQuery((signal) => listQueue({ limit: PAGE_SIZE, offset }, { signal }), [offset]);

  // Enrich queue rows with artist/title without a new backend endpoint: the
  // queue is always small (bounded by max_concurrent_downloads + whatever's
  // mid-retry-backoff), so fetching each active state once is cheap.
  useEffect(() => {
    const controller = new AbortController();
    Promise.all(
      LISTED_STATES.map((state) => listMediaAssets({ state, limit: 200 }, { signal: controller.signal })),
    )
      .then((results) => {
        if (controller.signal.aborted) return;
        const map = new Map<number, MediaAssetOut>();
        for (const rows of results) for (const row of rows) map.set(row.id, row);
        setAssetsByTrackId(map);
      })
      .catch(() => {
        // A page navigation aborting these in-flight lookups is expected
        // (the same pattern useApiQuery already handles) — nothing to do.
      });
    return () => controller.abort();
  }, [queue]);

  const columns: DataTableColumn<QueueItemOut>[] = [
    {
      key: 'track',
      header: 'Track',
      render: (item) => trackLabel(assetsByTrackId.get(item.media_asset_id), item.track_id),
    },
    {
      key: 'stage',
      header: 'Stage',
      render: (item) => <StatusBadge status={item.state} />,
    },
    {
      key: 'attempt',
      header: 'Attempt',
      render: (item) => {
        const attempt = latestAttempt(item);
        if (!attempt) return <span className="subtle-note">—</span>;
        return (
          <span className="subtle-note">
            #{attempt.attempt_number} · {attempt.status}
            {attempt.error_message ? ` — ${attempt.error_message}` : ''}
            {attempt.next_retry_at ? ` · next try ${new Date(attempt.next_retry_at).toLocaleString()}` : ''}
          </span>
        );
      },
    },
    {
      key: 'actions',
      header: '',
      align: 'right',
      render: (item) => <RowActions item={item} onDone={refetch} />,
    },
  ];

  return (
    <section>
      <div className="page-toolbar">
        <h1>Activity / Queue</h1>
      </div>

      {error && <ErrorBanner message={`Failed to load the queue: ${error}`} onRetry={refetch} />}

      <DataTable
        columns={columns}
        rows={queue ?? []}
        getRowKey={(item) => item.media_asset_id}
        loading={loading}
        emptyMessage="Nothing is currently downloading, processing, or importing."
        pagination={{
          limit: PAGE_SIZE,
          offset,
          hasMore: (queue?.length ?? 0) === PAGE_SIZE,
          onOffsetChange: setOffset,
        }}
      />
    </section>
  );
}
