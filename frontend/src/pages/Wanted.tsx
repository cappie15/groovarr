import { useState } from 'react';
import { ApiError } from '../api/client';
import { type WantedTrackOut, listWantedTracks, runSearchForAllWanted, runSearchForTrack } from '../api/search';
import { DataTable, type DataTableColumn } from '../components/DataTable';
import { ErrorBanner } from '../components/ErrorBanner';
import { useToast } from '../components/Toast';
import { useApiQuery } from '../hooks/useApiQuery';

const PAGE_SIZE = 50;

function errorMessage(err: unknown): string {
  return err instanceof ApiError ? err.message : 'Unexpected error — please try again.';
}

function formatDuration(ms: number): string {
  const totalSeconds = Math.round(ms / 1000);
  const m = Math.floor(totalSeconds / 60);
  const s = totalSeconds % 60;
  return `${m}:${s.toString().padStart(2, '0')}`;
}

/** A wanted track's own outcome copy — the row itself disappears from this
 * list the moment a search gives it any MediaAsset (Wanted = "no MediaAsset
 * row at all yet", per app.services.search's own definition), so the only
 * honest way to "reflect the outcome" is a toast describing what actually
 * happened, not a static spinner that would never resolve in place. */
function describeOutcome(outcome: { media_state: string; candidates_found: number }): string {
  switch (outcome.media_state) {
    case 'candidate_selected':
      return 'Matched automatically — queued for download.';
    case 'manual_review_required':
      return `Found ${outcome.candidates_found} candidate(s), none confident enough — sent to Manual Review.`;
    case 'missing':
      return 'No usable candidates found — still Missing.';
    default:
      return `Search finished (${outcome.media_state}).`;
  }
}

function SearchTrackButton({ track, onDone }: { track: WantedTrackOut; onDone: () => void }) {
  const toast = useToast();
  const [busy, setBusy] = useState(false);

  async function run() {
    setBusy(true);
    try {
      const outcome = await runSearchForTrack(track.id);
      toast.showInfo(`${track.canonical_artist} — ${track.canonical_title}: ${describeOutcome(outcome)}`);
      onDone();
    } catch (err) {
      toast.showError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <button type="button" className="btn btn--small btn--primary" disabled={busy} onClick={run}>
      {busy ? 'Searching…' : 'Search'}
    </button>
  );
}

export default function Wanted() {
  const [offset, setOffset] = useState(0);
  const toast = useToast();
  const [searchingAll, setSearchingAll] = useState(false);

  const { data, loading, error, refetch } = useApiQuery(
    (signal) => listWantedTracks({ limit: PAGE_SIZE, offset }, { signal }),
    [offset],
  );

  async function handleSearchAll() {
    setSearchingAll(true);
    try {
      const outcomes = await runSearchForAllWanted();
      const automatic = outcomes.filter((o) => o.media_state === 'candidate_selected').length;
      const manualReview = outcomes.filter((o) => o.media_state === 'manual_review_required').length;
      const missing = outcomes.filter((o) => o.media_state === 'missing').length;
      toast.showInfo(
        `Searched ${outcomes.length} wanted track(s): ${automatic} matched automatically, ` +
          `${manualReview} sent to Manual Review, ${missing} still missing.`,
      );
      refetch();
    } catch (err) {
      toast.showError(errorMessage(err));
    } finally {
      setSearchingAll(false);
    }
  }

  const columns: DataTableColumn<WantedTrackOut>[] = [
    {
      key: 'track',
      header: 'Track',
      sortAccessor: (t) => `${t.canonical_artist} ${t.canonical_title}`.toLowerCase(),
      render: (t) => (
        <div>
          <div style={{ fontWeight: 600 }}>{t.canonical_artist}</div>
          <div className="subtle-note">
            {t.canonical_title}
            {t.parsed_version ? ` (${t.parsed_version})` : ''}
            {t.explicit ? ' · Explicit' : ''}
          </div>
        </div>
      ),
    },
    {
      key: 'duration',
      header: 'Duration',
      render: (t) => <span className="subtle-note">{formatDuration(t.duration_ms)}</span>,
    },
    {
      key: 'playlists',
      header: 'Wanted by',
      render: (t) =>
        t.playlist_names.length > 0 ? (
          <span className="subtle-note">{t.playlist_names.join(', ')}</span>
        ) : (
          <span className="subtle-note">—</span>
        ),
    },
    {
      key: 'actions',
      header: '',
      align: 'right',
      render: (t) => <SearchTrackButton track={t} onDone={refetch} />,
    },
  ];

  return (
    <section>
      <div className="page-toolbar">
        <h1>Wanted / Missing</h1>
        <div className="btn-row">
          <button
            type="button"
            className="btn"
            disabled={searchingAll || (data?.length ?? 0) === 0}
            onClick={handleSearchAll}
          >
            {searchingAll ? 'Searching all…' : 'Search All Wanted'}
          </button>
        </div>
      </div>

      {error && <ErrorBanner message={`Failed to load wanted tracks: ${error}`} onRetry={refetch} />}

      <DataTable
        columns={columns}
        rows={data ?? []}
        getRowKey={(t) => t.id}
        loading={loading}
        emptyMessage="Nothing wanted right now — every playlist-referenced track has at least started acquisition."
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
