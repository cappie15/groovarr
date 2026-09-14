import type { ReactNode } from 'react';
import type { ScoreBreakdownEntry, VideoCandidateOut } from '../api/search';
import './CandidateScoreExplain.css';

/**
 * Renders one VideoCandidate as a thumbnail card with its deterministic
 * score + explanation (§21: "the UI should show WHY a candidate received
 * its score") — used by both the Automatic Search (read-only, showing what
 * was decided) and Manual Search (interactive, with a Select action passed
 * in via `actions`) pages, so the explanation logic lives in exactly one
 * place.
 */
export interface CandidateScoreExplainProps {
  candidate: VideoCandidateOut;
  /** Whether this candidate is the one that actually won (Automatic Search)
   * or was manually chosen — renders a distinct "automatic"/"selected" tone
   * on the score value instead of the neutral default. */
  isWinner?: boolean;
  automaticThreshold?: number;
  actions?: ReactNode;
}

function formatDuration(seconds: number | null): string {
  if (seconds == null) return 'unknown duration';
  const m = Math.floor(seconds / 60);
  const s = Math.round(seconds % 60);
  return `${m}:${s.toString().padStart(2, '0')}`;
}

export function CandidateScoreExplain({
  candidate,
  isWinner,
  automaticThreshold,
  actions,
}: CandidateScoreExplainProps) {
  const breakdown = (candidate.score_breakdown as ScoreBreakdownEntry[] | undefined) ?? [];
  const wouldBeAutomatic = automaticThreshold != null ? candidate.score >= automaticThreshold : undefined;
  const scoreTone =
    isWinner || wouldBeAutomatic === true
      ? 'candidate-score__value--automatic'
      : wouldBeAutomatic === false
        ? 'candidate-score__value--manual-review'
        : '';

  return (
    <div className="candidate-card">
      <img
        className="candidate-card__thumb"
        src={`https://i.ytimg.com/vi/${candidate.youtube_video_id}/hqdefault.jpg`}
        alt=""
        loading="lazy"
      />
      <div className="candidate-card__body">
        <p className="candidate-card__title">
          {candidate.title}
          {isWinner && ' ✓'}
        </p>
        <div className="candidate-card__meta">
          {candidate.channel_name ?? 'Unknown channel'} · {formatDuration(candidate.duration_s)} ·{' '}
          {candidate.orientation}
        </div>

        <div className="candidate-score">
          <span className={`candidate-score__value ${scoreTone}`}>{candidate.score}</span>
          <span className="candidate-score__label">
            confidence{automaticThreshold != null ? ` (auto ≥ ${automaticThreshold})` : ''}
          </span>
        </div>

        {breakdown.length > 0 && (
          <ul className="score-breakdown">
            {breakdown.map((entry, i) => (
              <li key={i} className="score-breakdown__line">
                <span
                  className={`score-breakdown__delta ${
                    entry.delta > 0 ? 'score-breakdown__delta--positive' : entry.delta < 0 ? 'score-breakdown__delta--negative' : ''
                  }`}
                >
                  {entry.delta > 0 ? `+${entry.delta}` : entry.delta}
                </span>
                <span className="score-breakdown__explanation">{entry.explanation || entry.signal}</span>
              </li>
            ))}
          </ul>
        )}

        {candidate.rejection_flags.length > 0 && (
          <div className="candidate-rejection-flags">
            {(candidate.rejection_flags as string[]).map((flag) => (
              <span key={flag} className="rejection-flag">
                {flag}
              </span>
            ))}
          </div>
        )}

        {actions}
      </div>
    </div>
  );
}
