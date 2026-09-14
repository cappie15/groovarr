import { useEffect, useMemo, useState } from 'react';
import { type MediaState, listMediaAssets } from '../api/media';
import { type VideoCandidateOut, getCandidatesForTrack } from '../api/search';
import { getSettings } from '../api/settings';
import { CandidateScoreExplain } from '../components/CandidateScoreExplain';
import { ErrorBanner } from '../components/ErrorBanner';
import { useApiQuery } from '../hooks/useApiQuery';

/**
 * "What did automatic matching decide, and why" (§21) — the states where an
 * automatic decision actually produced/kept a file. `manual_review_required`
 * is deliberately excluded: that's a track automatic matching declined to
 * decide on, which is what the Manual Search page is for.
 */
const AUTOMATIC_STATES: { value: MediaState; label: string }[] = [
  { value: 'candidate_selected', label: 'Candidate Selected (queued)' },
  { value: 'available', label: 'Available' },
  { value: 'incomplete', label: 'Incomplete (visualizer-class)' },
];

const PAGE_SIZE = 25;

export default function AutomaticSearch() {
  const [state, setState] = useState<MediaState>('candidate_selected');
  const [candidatesByTrack, setCandidatesByTrack] = useState<Record<number, VideoCandidateOut[]>>({});

  const { data, loading, error, refetch } = useApiQuery(
    (signal) => listMediaAssets({ state, limit: PAGE_SIZE }, { signal }),
    [state],
  );
  const settingsQuery = useApiQuery((signal) => getSettings({ signal }));

  // Only automatically-decided rows — a manually-selected asset (e.g. one
  // that went through Manual Search, or a manual replacement) isn't an
  // "automatic matching decision" even though it can share these states.
  const automaticAssets = useMemo(() => (data ?? []).filter((a) => !a.manual_selection), [data]);

  useEffect(() => {
    let cancelled = false;
    async function loadCandidates() {
      const withTrack = automaticAssets.filter((a) => a.track_id != null);
      const entries = await Promise.all(
        withTrack.map(async (a) => {
          try {
            return [a.track_id as number, await getCandidatesForTrack(a.track_id as number)] as const;
          } catch {
            return [a.track_id as number, []] as const;
          }
        }),
      );
      if (!cancelled) setCandidatesByTrack(Object.fromEntries(entries));
    }
    if (automaticAssets.length > 0) void loadCandidates();
    else setCandidatesByTrack({});
    return () => {
      cancelled = true;
    };
    // Re-run whenever the actual set of track ids shown changes.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [automaticAssets.map((a) => a.track_id).join(',')]);

  return (
    <section>
      <div className="page-toolbar">
        <h1>Automatic Search</h1>
        <div className="field" style={{ minWidth: 240 }}>
          <label htmlFor="automatic-state-filter">Show</label>
          <select
            id="automatic-state-filter"
            value={state}
            onChange={(e) => setState(e.target.value as MediaState)}
          >
            {AUTOMATIC_STATES.map((opt) => (
              <option key={opt.value} value={opt.value}>
                {opt.label}
              </option>
            ))}
          </select>
        </div>
      </div>

      {error && <ErrorBanner message={`Failed to load results: ${error}`} onRetry={refetch} />}

      {loading && <p className="subtle-note">Loading…</p>}

      {!loading && !error && automaticAssets.length === 0 && (
        <p className="subtle-note">No automatically-decided tracks in this state yet.</p>
      )}

      <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
        {automaticAssets.map((asset) => {
          const candidates = asset.track_id != null ? (candidatesByTrack[asset.track_id] ?? []) : [];
          const winner = candidates.find((c) => c.id === asset.video_candidate_id);
          return (
            <div key={asset.id}>
              <div style={{ marginBottom: 6, fontWeight: 600 }}>
                {asset.track_artist ?? 'Unknown artist'} — {asset.track_title ?? 'Unknown title'}
              </div>
              {winner ? (
                <CandidateScoreExplain
                  candidate={winner}
                  isWinner
                  automaticThreshold={settingsQuery.data?.automatic_match_threshold}
                />
              ) : (
                <p className="subtle-note">
                  No stored candidate for this asset (it may have been imported via the existing-library
                  scan rather than automatic search).
                </p>
              )}
            </div>
          );
        })}
      </div>
    </section>
  );
}
