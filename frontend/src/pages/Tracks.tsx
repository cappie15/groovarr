import { useState } from 'react';
import { Link } from 'react-router-dom';
import { ApiError } from '../api/client';
import {
  type MediaAssetOut,
  type MediaState,
  listMediaAssets,
  organizeMediaAsset,
  triggerLibraryScan,
} from '../api/media';
import { DataTable, type DataTableColumn } from '../components/DataTable';
import { ErrorBanner } from '../components/ErrorBanner';
import { StatusBadge } from '../components/StatusBadge';
import { useToast } from '../components/Toast';
import { useApiQuery } from '../hooks/useApiQuery';

const PAGE_SIZE = 50;

const STATE_OPTIONS: { value: MediaState | ''; label: string }[] = [
  { value: '', label: 'All states' },
  { value: 'wanted', label: 'Wanted' },
  { value: 'searching', label: 'Searching' },
  { value: 'manual_review_required', label: 'Manual Review Required' },
  { value: 'candidate_selected', label: 'Candidate Selected' },
  { value: 'queued', label: 'Queued' },
  { value: 'downloading', label: 'Downloading' },
  { value: 'processing', label: 'Processing' },
  { value: 'importing', label: 'Importing' },
  { value: 'available', label: 'Available' },
  { value: 'incomplete', label: 'Incomplete' },
  { value: 'download_failed', label: 'Download Failed' },
  { value: 'missing', label: 'Missing' },
];

function errorMessage(err: unknown): string {
  return err instanceof ApiError ? err.message : 'Unexpected error — please try again.';
}

function formatBytes(bytes: number | null): string {
  if (bytes == null) return '—';
  const units = ['B', 'KB', 'MB', 'GB'];
  let value = bytes;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  return `${value.toFixed(unit === 0 ? 0 : 1)} ${units[unit]}`;
}

function formatDuration(seconds: number | null): string {
  if (seconds == null) return '—';
  const m = Math.floor(seconds / 60);
  const s = Math.round(seconds % 60);
  return `${m}:${s.toString().padStart(2, '0')}`;
}

function OrganizeAction({ asset, onOrganized }: { asset: MediaAssetOut; onOrganized: () => void }) {
  const toast = useToast();
  const [busy, setBusy] = useState(false);

  if (!asset.needs_organize) return null;

  async function organize() {
    setBusy(true);
    try {
      await organizeMediaAsset(asset.id);
      toast.showInfo('File renamed to match the naming template.');
      onOrganized();
    } catch (err) {
      toast.showError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <button type="button" className="btn btn--small" disabled={busy} onClick={organize}>
      {busy ? 'Organizing…' : 'Organize / Rename'}
    </button>
  );
}

export default function Tracks() {
  const [state, setState] = useState<MediaState | ''>('');
  const [offset, setOffset] = useState(0);
  const toast = useToast();
  const [scanning, setScanning] = useState(false);

  const { data, loading, error, refetch } = useApiQuery(
    (signal) =>
      listMediaAssets(
        { state: state || undefined, limit: PAGE_SIZE, offset },
        { signal },
      ),
    [state, offset],
  );

  async function handleScan() {
    setScanning(true);
    try {
      const result = await triggerLibraryScan();
      toast.showInfo(
        `Scan complete: ${result.files_scanned} file(s) scanned, ${result.matched_available} matched, ` +
          `${result.manual_review_created} sent to Manual Review, ${result.skipped_no_signal} skipped.`,
      );
      refetch();
    } catch (err) {
      toast.showError(errorMessage(err));
    } finally {
      setScanning(false);
    }
  }

  const columns: DataTableColumn<MediaAssetOut>[] = [
    {
      key: 'track',
      header: 'Track',
      render: (a) =>
        a.track_artist || a.track_title ? (
          <div>
            <div style={{ fontWeight: 600 }}>{a.track_artist ?? 'Unknown artist'}</div>
            <div className="subtle-note">
              {a.track_title ?? 'Unknown title'}
              {a.track_release_year ? ` (${a.track_release_year})` : ''}
            </div>
          </div>
        ) : (
          <span className="subtle-note">Not yet matched to a track</span>
        ),
    },
    { key: 'state', header: 'State', render: (a) => <StatusBadge status={a.state} /> },
    {
      key: 'file',
      header: 'File',
      render: (a) => (
        <div className="subtle-note">
          {a.container ? a.container.toUpperCase() : '—'} · {a.resolution_label ?? 'Unknown quality'}
          <br />
          {formatDuration(a.duration_s)} · {formatBytes(a.file_size)}
        </div>
      ),
    },
    {
      key: 'manual_selection',
      header: 'Manual',
      render: (a) => (a.manual_selection ? <StatusBadge status="synced" label="Manual Selection" /> : '—'),
    },
    {
      key: 'jellyfin_sync_status',
      header: 'Jellyfin',
      render: (a) => <StatusBadge status={a.jellyfin_sync_status} />,
    },
    {
      key: 'plex_sync_status',
      header: 'Plex',
      render: (a) => <StatusBadge status={a.plex_sync_status} />,
    },
    {
      key: 'actions',
      header: '',
      align: 'right',
      render: (a) => (
        <div className="btn-row" style={{ justifyContent: 'flex-end' }}>
          {a.state === 'manual_review_required' && (a.candidate_track_id ?? a.track_id) != null && (
            <Link
              className="btn btn--small btn--primary"
              to={`/search/manual?trackId=${a.candidate_track_id ?? a.track_id}`}
            >
              Manual Search
            </Link>
          )}
          <OrganizeAction asset={a} onOrganized={refetch} />
        </div>
      ),
    },
  ];

  return (
    <section>
      <div className="page-toolbar">
        <h1>Tracks</h1>
        <div className="btn-row">
          <div className="field" style={{ minWidth: 220 }}>
            <label htmlFor="state-filter">Filter by state</label>
            <select
              id="state-filter"
              value={state}
              onChange={(e) => {
                setState(e.target.value as MediaState | '');
                setOffset(0);
              }}
            >
              {STATE_OPTIONS.map((opt) => (
                <option key={opt.value} value={opt.value}>
                  {opt.label}
                </option>
              ))}
            </select>
          </div>
          <button type="button" className="btn" disabled={scanning} onClick={handleScan}>
            {scanning ? 'Scanning…' : 'Scan Existing Library'}
          </button>
        </div>
      </div>

      {error && <ErrorBanner message={`Failed to load tracks: ${error}`} onRetry={refetch} />}

      <DataTable
        columns={columns}
        rows={data ?? []}
        getRowKey={(a) => a.id}
        loading={loading}
        emptyMessage="No media tracked yet — connect a playlist or run a library scan to get started."
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
