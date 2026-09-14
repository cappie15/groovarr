# 0009 — Matching/scoring: deterministic weighted scoring, not ML

Status: Accepted
Rationale: [architecture review §8, §19-25](../00-research-and-architecture-review.md)

## Context

The master spec explicitly requires matching decisions to be deterministic and explainable —
"do not hide important decisions behind opaque AI/LLM matching" — with version/remix correctness
allowed to outrank generic "official video" status, and false positives treated as strictly worse
than a Manual Review item.

## Decision

`app/matching/scoring.py`'s `score_candidate` is a pure function: given a `Track` and an enriched
candidate, it returns a `ScoreResult` whose signals are individually named, weighted, and recorded
with a human-readable `explanation` string each — no model inference anywhere in the path. Hard
filters (Shorts/portrait) are applied before scoring even runs, never as a scored-but-low signal.
Confidence classification against a configurable `automatic_match_threshold` (Settings, default
70) decides `CandidateSelected` vs. `ManualReviewRequired`.

## Consequences

- Every automatic decision is fully inspectable via the persisted `score_breakdown` — this is what
  the frontend's `CandidateScoreExplain` component renders directly as "+Exact artist match"-style
  reasoning, with no separate explanation-generation step needed at render time.
- Tuning the threshold is a fixture-corpus exercise (covering remix-vs-official, covers, live/lyric
  videos, visualizers, Unicode/alias artists, featured artists — see `backend/tests/unit/test_scoring.py`),
  not a value picked by feel.
- A manually-selected candidate (`MediaAsset.manual_selection=True`) is a persistent override that
  automatic re-runs must never silently touch — enforced in `app/services/search.py`.
