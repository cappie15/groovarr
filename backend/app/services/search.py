"""Automatic Search, Manual Search, and Manual Selection (§19-27/§33-34/§37).

Orchestrates: discovery -> yt-dlp enrichment -> hard filters -> scoring ->
classification -> MediaAsset state transition. Manual selections are
persisted as a deliberate override (§33) that a later *automatic* run must
never silently replace — only the explicit "search again" entry point may
reopen an already-classified or manually-selected track (§34/§37).

Scoping note: "wanted" tracks, for the batch automatic-search endpoint, are
tracks referenced by at least one PlaylistEntry with *no* MediaAsset row at
all yet — i.e. genuinely untouched. A track whose MediaAsset already landed
in MISSING/ManualReviewRequired is not swept up by the batch endpoint (that
would either hammer external APIs on every batch run for a track that
already failed, or silently override a pending human decision); those need
an explicit "search again" or "manual selection" call instead. The full
retry/backoff policy for MISSING tracks belongs to Phase 5 (§50) — this
phase only discovers and scores candidates, it never downloads anything.
"""

import asyncio
import random
from dataclasses import dataclass

import httpx
import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.candidates import VideoCandidate
from app.db.models.media import MediaAsset, MediaState
from app.db.models.spotify import PlaylistEntry, Track
from app.domain.reference_counting import sync_references_for_track
from app.integrations.youtube.discovery import discover_candidates
from app.integrations.youtube.errors import YouTubeError
from app.integrations.youtube.ytdlp_client import enrich_candidate
from app.matching.scoring import classify, hard_filter, score_candidate
from app.services.history import record_event
from app.services.settings_service import get_app_settings

logger = structlog.get_logger(__name__)


class TrackNotFoundError(ValueError):
    """Raised when an API caller references a track_id that doesn't exist."""


class CandidateNotFoundError(ValueError):
    """Raised when a Manual Selection references a candidate that doesn't
    exist, or exists for a different track than the one requested.
    """


class CandidateSelectionConflictError(ValueError):
    """Raised when Manual Selection (`select_candidate`) is invoked against a
    `MediaAsset` it cannot safely mutate: either a real file already exists
    for it (`/replace` is the safe path for that, per `app.services.
    replacement`) or an acquisition attempt is already in flight for it
    (QUEUED/DOWNLOADING/PROCESSING/IMPORTING).

    A live-testing session (2026-09-14) hit the in-flight case for real:
    Automatic Search picked a winner, the background download queue claimed
    it (DOWNLOADING) within its normal poll tick, and a Manual Selection
    call landed microseconds later — `select_candidate` used to reset the
    row straight back to CANDIDATE_SELECTED with a *different*
    `video_candidate_id`/`source_reference` unconditionally, with no check
    of the row's current state at all. The in-flight download (for the
    *original* winner) kept running against that same row and, once it
    finished, called `_import_asset` and overwrote `local_path`/state with
    *its* result — silently discarding the second (Manual Selection's)
    result, which had already finished importing moments earlier. Both were
    real, fully downloaded, muxed, tagged files; whichever import step lost
    the race left its own file on disk referenced by nothing in the
    database at all — not even eligible for the normal reference-counted
    delete path (`app.services.deletion`), since nothing ever points a
    `MediaAsset` row back at it. Refusing the second call outright — the
    same defensive pattern already used by `app.services.acquisition.
    enqueue_for_download`/`retry_download`/`cancel_queued` for every other
    state-machine transition in this module — closes the race instead of
    leaving its outcome to whichever download happens to finish last.
    """


@dataclass
class SearchOutcome:
    media_asset: MediaAsset
    candidates_found: int
    winning_candidate: VideoCandidate | None


async def get_wanted_tracks(session: AsyncSession, *, limit: int | None = 100, offset: int = 0) -> list[Track]:
    """Tracks referenced by at least one PlaylistEntry with no MediaAsset
    row at all yet — see module docstring for why this excludes tracks
    already in MISSING/ManualReviewRequired.

    Paginated by default (§94, Phase 10 audit): a large connected library
    can have thousands of tracks. Pass `limit=None` for the rare caller that
    genuinely needs every row in one go (`run_search_for_all_wanted`, below —
    a batch sweep must not silently stop at page 1).

    Frontend-wiring fix: a ManualReviewRequired row created by the existing-
    library scanner (app.services.library_scan) leaves `track_id` null and
    only sets `candidate_track_id` (§7 — the association isn't confirmed
    yet), so excluding purely on `MediaAsset.track_id` let such a track leak
    back into "wanted" — worse, `_get_or_create_media_asset` keys strictly
    on `track_id` too, so running Automatic Search on it (e.g. via "Search
    All Wanted") would have inserted a *second*, conflicting MediaAsset for
    the same track rather than finding the pending review row. Both
    denormalization columns must be excluded.
    """
    already_has_asset = select(MediaAsset.track_id).where(MediaAsset.track_id.is_not(None)).union(
        select(MediaAsset.candidate_track_id).where(MediaAsset.candidate_track_id.is_not(None))
    )
    query = (
        select(Track)
        .where(Track.id.in_(select(PlaylistEntry.track_id).distinct()))
        .where(Track.id.not_in(already_has_asset))
        .order_by(Track.id)
        .offset(offset)
    )
    if limit is not None:
        query = query.limit(limit)
    result = await session.execute(query)
    return list(result.scalars().all())


async def _get_or_create_media_asset(session: AsyncSession, track_id: int) -> MediaAsset:
    asset = await session.scalar(select(MediaAsset).where(MediaAsset.track_id == track_id).order_by(MediaAsset.id))
    if asset is None:
        asset = MediaAsset(track_id=track_id, state=MediaState.SEARCHING)
        session.add(asset)
        await session.flush()
    # Phase 9 fix: every caller of this helper is establishing "this track
    # now has a MediaAsset" — ensure every playlist that currently lists the
    # track via PlaylistEntry can see it via PlaylistMediaReference, which is
    # what reference-counted deletion (app.domain.reference_counting) and
    # Phase 8's playlist reconstruction both key on. Idempotent, cheap no-op
    # on repeat calls.
    await sync_references_for_track(session, track_id, asset.id)
    return asset


async def run_search_for_track(
    session: AsyncSession, http: httpx.AsyncClient, track: Track, *, reopen: bool = False
) -> SearchOutcome:
    """Run Automatic Search for one track: discover, enrich, hard-filter,
    score, persist every surviving candidate, and classify the result.

    `reopen=True` is what "Search Again" (§37) uses — it bypasses the
    "manual_selection already set" guard a plain automatic run must respect
    (§33/§34), and clears `manual_selection` since the track is
    deliberately being reopened by an explicit user action.
    """
    asset = await _get_or_create_media_asset(session, track.id)
    if asset.manual_selection and not reopen:
        # A human already made a deliberate choice (§33) — never silently
        # override it from an automatic run.
        return SearchOutcome(media_asset=asset, candidates_found=0, winning_candidate=None)

    if reopen:
        asset.manual_selection = False

    # See the `has_existing_file` comment further down: a track that already
    # has a real, currently-serving file must never have its own state
    # mutated by a re-search — SEARCHING would make it look unavailable
    # even though the file is still sitting right there on disk.
    if asset.local_path is None:
        asset.state = MediaState.SEARCHING
        await session.flush()

    try:
        raw_candidates = await discover_candidates(session, http, track)
    except YouTubeError as exc:
        logger.warning("search.discovery_failed", track_id=track.id, error=str(exc))
        raw_candidates = []

    settings_row = await get_app_settings(session)
    threshold = settings_row.automatic_match_threshold

    # Drop any candidates from a previous run for this track so re-search
    # doesn't accumulate stale rows alongside fresh ones.
    old_candidates = (await session.scalars(select(VideoCandidate).where(VideoCandidate.track_id == track.id))).all()
    for old in old_candidates:
        await session.delete(old)
    await session.flush()

    scored: list[tuple[VideoCandidate, int, bool]] = []  # (row, score, is_visualizer_only)
    for raw in raw_candidates:
        try:
            enriched = await enrich_candidate(raw.youtube_video_id)
        except YouTubeError as exc:
            logger.info("search.enrich_failed_skipping_candidate", video_id=raw.youtube_video_id, error=str(exc))
            continue

        exclusion_reason = hard_filter(enriched)
        if exclusion_reason is not None:
            logger.info("search.candidate_hard_filtered", video_id=raw.youtube_video_id, reason=exclusion_reason)
            continue

        result = score_candidate(track, raw, enriched)
        candidate_row = VideoCandidate(
            track_id=track.id,
            youtube_video_id=raw.youtube_video_id,
            title=raw.title,
            channel_id=raw.channel_id,
            channel_name=raw.channel_name,
            duration_s=enriched.duration_s,
            orientation=enriched.orientation,
            official_signals=result.official_signals,
            score=result.total,
            score_breakdown=[
                {"signal": s.signal, "delta": s.delta, "explanation": s.explanation} for s in result.breakdown
            ],
            rejection_flags=result.rejection_flags,
        )
        session.add(candidate_row)
        scored.append((candidate_row, result.total, result.is_visualizer_only))

    await session.flush()

    # Phase 9 (§36/§37): once a track already has a real, currently-serving
    # file (Available or Incomplete), a re-search (Search Again, or the
    # upgrade monitor's periodic pass) must NEVER mutate this asset's own
    # state/video_candidate_id/local_path directly — `_import_asset` (Phase
    # 5) has no notion of "retire the file this row used to point to", so
    # flipping this row back into the download queue here would silently
    # orphan the still-perfectly-good current file on disk the moment a new
    # one lands (or, on a transient zero-candidates/manual-review result,
    # would wrongly demote a working file to MISSING/ManualReview even
    # though it's still sitting right there on disk). A genuine replacement
    # for an already-downloaded track must go through
    # `app.services.replacement.replace_track_media` instead, which keeps
    # the OLD file fully intact and serving for the entire duration of the
    # new download and only swaps/deletes it once the new one is proven
    # good. This function still discovers/scores/persists candidates and
    # returns the winner either way — callers that want to *apply* a winning
    # candidate to an already-downloaded track call `replace_track_media`
    # themselves with it.
    has_existing_file = asset.local_path is not None

    if not scored:
        if not has_existing_file:
            asset.state = MediaState.MISSING
            asset.video_candidate_id = None
            record_event(
                session,
                track_id=track.id,
                media_asset_id=asset.id,
                event_type="search.missing",
                detail="No candidates survived discovery/hard-filtering.",
            )
            await session.commit()
        return SearchOutcome(media_asset=asset, candidates_found=0, winning_candidate=None)

    winner, winner_score, winner_is_visualizer = max(scored, key=lambda t: t[1])
    decision = classify(winner_score, threshold)

    if decision == "automatic" and not has_existing_file:
        asset.video_candidate_id = winner.id
        asset.source_reference = f"youtube:{winner.youtube_video_id}"
        # A visualizer-only winner is still queued for download like any
        # other automatic match (§26 — "may be acquired when no proper
        # music video exists"); it's flagged here so Phase 5's import step
        # lands the *acquired* file on INCOMPLETE instead of AVAILABLE,
        # keeping it eligible for Phase 9's upgrade monitoring, rather than
        # skipping acquisition entirely.
        asset.candidate_is_visualizer_only = winner_is_visualizer
        asset.state = MediaState.CANDIDATE_SELECTED
        record_event(
            session,
            track_id=track.id,
            media_asset_id=asset.id,
            event_type="search.automatic_selected",
            detail=(
                f"Automatic match: {winner.title!r} (score {winner_score}"
                f"{', visualizer-only' if winner_is_visualizer else ''})"
            ),
        )
    elif decision != "automatic" and not has_existing_file:
        asset.state = MediaState.MANUAL_REVIEW_REQUIRED
        asset.video_candidate_id = None
        record_event(
            session,
            track_id=track.id,
            media_asset_id=asset.id,
            event_type="search.manual_review_required",
            detail=f"Best candidate {winner.title!r} scored {winner_score}, below the automatic threshold.",
        )
    # else (has_existing_file, either decision): purely informational for
    # this call — see the comment above `has_existing_file`.

    await session.commit()
    logger.info(
        "search.completed",
        track_id=track.id,
        candidates=len(scored),
        winner_score=winner_score,
        decision=decision,
        state=asset.state.value,
    )
    return SearchOutcome(
        media_asset=asset, candidates_found=len(scored), winning_candidate=winner if decision == "automatic" else None
    )


# Jittered pause between tracks in a bulk "Search All Wanted" sweep only —
# a single-track search (run_search_for_track called directly, e.g. from
# the Manual/Automatic Search UI for one track) is never paced.
#
# Exists because a live deployment of this project hit a real
# `HTTP Error 403: Forbidden` from YouTube during testing, consistent with
# anti-bot rate-limiting — a burst of many requests right after a large
# Spotify playlist import (which can make hundreds of tracks "Wanted" at
# once) makes that materially worse. The project deliberately chose
# cooperative request pacing over any form of proxy rotation or other
# detection-evasion technique (discussed and explicitly rejected — public/
# free proxies are themselves heavily blocked and unreliable, and Groovarr
# should be a good API citizen rather than evade rate-limiting on
# principle). Mirrors the jitter pattern already used by the Spotify sync
# scheduler and LRCLIB's client-side rate limiter elsewhere in this
# codebase.
_BULK_SEARCH_MIN_DELAY_S = 2.0
_BULK_SEARCH_MAX_DELAY_S = 5.0


async def _pace_bulk_search() -> None:
    delay = random.uniform(_BULK_SEARCH_MIN_DELAY_S, _BULK_SEARCH_MAX_DELAY_S)
    logger.info("search.bulk_pacing_delay", delay_s=round(delay, 2))
    await asyncio.sleep(delay)


async def run_search_for_all_wanted(session: AsyncSession, http: httpx.AsyncClient) -> list[SearchOutcome]:
    tracks = await get_wanted_tracks(session, limit=None)
    outcomes: list[SearchOutcome] = []
    for i, track in enumerate(tracks):
        if i > 0:
            await _pace_bulk_search()
        outcomes.append(await run_search_for_track(session, http, track))
    return outcomes


async def get_candidates_for_track(session: AsyncSession, track_id: int) -> list[VideoCandidate]:
    result = await session.execute(
        select(VideoCandidate).where(VideoCandidate.track_id == track_id).order_by(VideoCandidate.score.desc())
    )
    return list(result.scalars().all())


_SELECT_UNSAFE_STATES = (
    MediaState.QUEUED,
    MediaState.DOWNLOADING,
    MediaState.PROCESSING,
    MediaState.IMPORTING,
)


async def select_candidate(session: AsyncSession, track_id: int, video_candidate_id: int) -> MediaAsset:
    """Manual Selection (§33/§34): a persistent, intentional override that a
    later automatic run must never silently replace (enforced in
    `run_search_for_track` via `MediaAsset.manual_selection`).

    Only safe to apply directly to the `MediaAsset` row when nothing else
    could be concurrently or already writing `local_path` for it — see
    `CandidateSelectionConflictError`. A track that already has a real file
    must go through `app.services.replacement.replace_track_media` (the
    `/replace` endpoint) instead, and a track with an acquisition attempt
    currently in flight must be left to finish (or be Cancelled, if still
    only QUEUED) before it can be reselected.
    """
    candidate = await session.get(VideoCandidate, video_candidate_id)
    if candidate is None or candidate.track_id != track_id:
        raise CandidateNotFoundError(f"No candidate {video_candidate_id} found for track {track_id}")

    asset = await _get_or_create_media_asset(session, track_id)
    if asset.local_path is not None:
        raise CandidateSelectionConflictError(
            f"Track {track_id} already has a downloaded file — use Replace instead of Manual Selection."
        )
    if asset.state in _SELECT_UNSAFE_STATES:
        raise CandidateSelectionConflictError(
            f"Track {track_id} has a download already in progress (state={asset.state.value}) — "
            "wait for it to finish (or Cancel it first, if still Queued) before selecting a different candidate."
        )

    asset.video_candidate_id = candidate.id
    asset.source_reference = f"youtube:{candidate.youtube_video_id}"
    asset.manual_selection = True
    # An explicit human choice is never auto-downgraded to Incomplete, even
    # if the chosen candidate happens to be a visualizer — the user knows
    # what they picked (§33), so this always lands on AVAILABLE once
    # acquired, never INCOMPLETE.
    asset.candidate_is_visualizer_only = False
    asset.state = MediaState.CANDIDATE_SELECTED
    record_event(
        session,
        track_id=track_id,
        media_asset_id=asset.id,
        event_type="search.manual_selection_made",
        detail=f"Manually selected {candidate.title!r} ({candidate.youtube_video_id}).",
    )
    await session.commit()
    return asset
