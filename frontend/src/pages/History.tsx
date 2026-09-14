import { useMemo, useState } from 'react';
import { listHistory, type HistoryEventOut } from '../api/history';
import { DataTable, type DataTableColumn } from '../components/DataTable';
import { ErrorBanner } from '../components/ErrorBanner';
import { useApiQuery } from '../hooks/useApiQuery';

const PAGE_SIZE = 50;

function formatEventType(eventType: string): string {
  // e.g. "acquisition.download_started" -> "Acquisition › Download Started".
  // Each dot-separated part is humanized independently before joining, so a
  // part's own underscores don't bleed across the "›" separator.
  return eventType
    .split('.')
    .map((part) =>
      part
        .split('_')
        .map((word) => word.charAt(0).toUpperCase() + word.slice(1))
        .join(' '),
    )
    .join(' › ');
}

export default function History() {
  const [offset, setOffset] = useState(0);
  const [trackIdFilter, setTrackIdFilter] = useState('');

  const parsedTrackId = trackIdFilter.trim() === '' ? undefined : Number(trackIdFilter.trim());
  const validTrackFilter = parsedTrackId !== undefined && Number.isFinite(parsedTrackId) ? parsedTrackId : undefined;

  const { data, loading, error, refetch } = useApiQuery(
    (signal) => listHistory({ track_id: validTrackFilter, limit: PAGE_SIZE, offset }, { signal }),
    [offset, validTrackFilter],
  );

  const columns = useMemo<DataTableColumn<HistoryEventOut>[]>(
    () => [
      {
        key: 'occurred_at',
        header: 'When',
        sortAccessor: (e) => e.occurred_at,
        render: (e) => <span className="subtle-note">{new Date(e.occurred_at).toLocaleString()}</span>,
      },
      {
        key: 'event_type',
        header: 'Event',
        render: (e) => <strong>{formatEventType(e.event_type)}</strong>,
      },
      {
        key: 'detail',
        header: 'Detail',
        render: (e) => e.detail ?? <span className="subtle-note">—</span>,
      },
      {
        key: 'track',
        header: 'Track / Asset',
        render: (e) => (
          <span className="subtle-note">
            {e.track_id ? `Track #${e.track_id}` : ''}
            {e.track_id && e.media_asset_id ? ' · ' : ''}
            {e.media_asset_id ? `Media #${e.media_asset_id}` : ''}
            {!e.track_id && !e.media_asset_id ? '—' : ''}
          </span>
        ),
      },
    ],
    [],
  );

  return (
    <section>
      <div className="page-toolbar">
        <h1>History</h1>
        <div className="field" style={{ minWidth: 180 }}>
          <label htmlFor="history-track-filter">Filter by track ID</label>
          <input
            id="history-track-filter"
            type="text"
            inputMode="numeric"
            placeholder="e.g. 42"
            value={trackIdFilter}
            onChange={(e) => {
              setOffset(0);
              setTrackIdFilter(e.target.value);
            }}
          />
        </div>
      </div>

      {error && <ErrorBanner message={`Failed to load history: ${error}`} onRetry={refetch} />}

      <DataTable
        columns={columns}
        rows={data ?? []}
        getRowKey={(e) => e.id}
        loading={loading}
        emptyMessage="Nothing has happened yet — acquisitions, replacements, and deletions will show up here."
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
