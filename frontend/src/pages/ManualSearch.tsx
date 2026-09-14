import { useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { ApiError } from '../api/client';
import { type MediaAssetOut, getMediaAssetForTrack, listMediaAssets } from '../api/media';
import { getCandidatesForTrack, replaceTrackMedia, selectCandidate } from '../api/search';
import { getSettings } from '../api/settings';
import { CandidateScoreExplain } from '../components/CandidateScoreExplain';
import { ErrorBanner } from '../components/ErrorBanner';
import { useToast } from '../components/Toast';
import { useApiQuery } from '../hooks/useApiQuery';

const PAGE_SIZE = 50;

function errorMessage(err: unknown): string {
  return err instanceof ApiError ? err.message : 'Unexpected error — please try again.';
}

/** No track chosen yet — offer every track currently in Manual Review as an
 * entry point (the Tracks page also links here directly with `?trackId=`
 * for a specific one). */
function TrackPicker({ onPick }: { onPick: (trackId: number) => void }) {
  const { data, loading, error, refetch } = useApiQuery(
    (signal) => listMediaAssets({ state: 'manual_review_required', limit: PAGE_SIZE }, { signal }),
  );

  return (
    <section>
      <div className="page-toolbar">
        <h1>Manual Search</h1>
      </div>
      <p className="subtle-note" style={{ marginBottom: 16 }}>
        Pick a track currently in Manual Review to inspect its candidates and choose one yourself.
      </p>

      {error && <ErrorBanner message={`Failed to load tracks needing review: ${error}`} onRetry={refetch} />}
      {loading && <p className="subtle-note">Loading…</p>}
      {!loading && !error && (data ?? []).length === 0 && (
        <p className="subtle-note">Nothing is waiting for Manual Review right now.</p>
      )}

      <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
        {(data ?? []).map((asset) => {
          const trackId = asset.candidate_track_id ?? asset.track_id;
          if (trackId == null) return null;
          return (
            <button
              key={asset.id}
              type="button"
              className="btn"
              style={{ textAlign: 'left', justifyContent: 'flex-start' }}
              onClick={() => onPick(trackId)}
            >
              <strong>{asset.track_artist ?? 'Unknown artist'}</strong> — {asset.track_title ?? 'Unknown title'}
              {asset.review_reason && <span className="subtle-note"> · {asset.review_reason}</span>}
            </button>
          );
        })}
      </div>
    </section>
  );
}

function SelectButton({
  trackId,
  candidateId,
  hasExistingFile,
  onDone,
}: {
  trackId: number;
  candidateId: number;
  hasExistingFile: boolean;
  onDone: (outcomeState: string) => void;
}) {
  const toast = useToast();
  const [busy, setBusy] = useState(false);
  const [confirmingReplace, setConfirmingReplace] = useState(false);

  async function doSelect() {
    setBusy(true);
    try {
      const outcome = await selectCandidate(trackId, candidateId);
      toast.showInfo('Candidate selected — queued for download.');
      onDone(outcome.media_state);
    } catch (err) {
      toast.showError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  async function doReplace() {
    setBusy(true);
    try {
      const outcome = await replaceTrackMedia(trackId, candidateId);
      toast.showInfo('Replacement started — the current file keeps working until the new one is ready.');
      setConfirmingReplace(false);
      onDone(outcome.media_state);
    } catch (err) {
      toast.showError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  if (!hasExistingFile) {
    return (
      <button type="button" className="btn btn--small btn--primary" disabled={busy} onClick={doSelect}>
        {busy ? 'Selecting…' : 'Select'}
      </button>
    );
  }

  if (!confirmingReplace) {
    return (
      <button type="button" className="btn btn--small btn--primary" onClick={() => setConfirmingReplace(true)}>
        Replace Existing File…
      </button>
    );
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 6, alignItems: 'flex-end' }}>
      <span className="subtle-note" style={{ maxWidth: 220, textAlign: 'right' }}>
        This track already has a working file. The old one stays in place until the new download is
        fully validated.
      </span>
      <div className="btn-row">
        <button type="button" className="btn btn--small" disabled={busy} onClick={() => setConfirmingReplace(false)}>
          Cancel
        </button>
        <button type="button" className="btn btn--small btn--danger" disabled={busy} onClick={doReplace}>
          {busy ? 'Replacing…' : 'Confirm Replace'}
        </button>
      </div>
    </div>
  );
}

function TrackCandidates({ trackId, onSwitchTrack }: { trackId: number; onSwitchTrack: () => void }) {
  const toast = useToast();
  const candidatesQuery = useApiQuery((signal) => getCandidatesForTrack(trackId, { signal }), [trackId]);
  const assetQuery = useApiQuery((signal) => getMediaAssetForTrack(trackId, { signal }), [trackId]);
  const settingsQuery = useApiQuery((signal) => getSettings({ signal }));

  const asset: MediaAssetOut | null = assetQuery.data ?? null;
  const hasExistingFile = asset?.local_path != null;

  function handleDone(outcomeState: string) {
    if (outcomeState !== 'manual_review_required') {
      toast.showInfo('This track has left Manual Review.');
    }
    candidatesQuery.refetch();
    assetQuery.refetch();
  }

  return (
    <section>
      <div className="page-toolbar">
        <h1>Manual Search</h1>
        <button type="button" className="btn btn--small" onClick={onSwitchTrack}>
          ← Choose a different track
        </button>
      </div>

      {asset && (
        <p className="subtle-note" style={{ marginBottom: 16 }}>
          {asset.track_artist ?? 'Unknown artist'} — {asset.track_title ?? 'Unknown title'}
          {asset.review_reason ? ` · ${asset.review_reason}` : ''}
        </p>
      )}

      {candidatesQuery.error && (
        <ErrorBanner message={`Failed to load candidates: ${candidatesQuery.error}`} onRetry={candidatesQuery.refetch} />
      )}
      {candidatesQuery.loading && <p className="subtle-note">Loading candidates…</p>}
      {!candidatesQuery.loading && !candidatesQuery.error && (candidatesQuery.data ?? []).length === 0 && (
        <p className="subtle-note">No candidates were found for this track.</p>
      )}

      <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
        {(candidatesQuery.data ?? []).map((candidate) => (
          <CandidateScoreExplain
            key={candidate.id}
            candidate={candidate}
            isWinner={asset?.video_candidate_id === candidate.id}
            automaticThreshold={settingsQuery.data?.automatic_match_threshold}
            actions={
              <div style={{ marginTop: 8, display: 'flex', justifyContent: 'flex-end' }}>
                <SelectButton
                  trackId={trackId}
                  candidateId={candidate.id}
                  hasExistingFile={hasExistingFile}
                  onDone={handleDone}
                />
              </div>
            }
          />
        ))}
      </div>
    </section>
  );
}

export default function ManualSearch() {
  const [params, setParams] = useSearchParams();
  const trackIdParam = params.get('trackId');
  const trackId = trackIdParam ? Number(trackIdParam) : null;

  if (trackId == null || Number.isNaN(trackId)) {
    return <TrackPicker onPick={(id) => setParams({ trackId: String(id) })} />;
  }

  return <TrackCandidates trackId={trackId} onSwitchTrack={() => setParams({})} />;
}
