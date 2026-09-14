"""Automatic Search, Manual Search, and Manual Selection endpoints
(§19-27/§33-34/§37). See app/services/search.py for the orchestration logic
and app/matching/scoring.py for the scoring engine these expose.
"""

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_http_client
from app.api.pagination import Pagination, pagination_params
from app.db.models.candidates import VideoCandidate
from app.db.models.media import MediaState
from app.db.models.spotify import PlaylistEntry, SpotifyPlaylist, Track
from app.db.session import get_session
from app.services.replacement import ReplacementError, replace_track_media
from app.services.search import (
    CandidateNotFoundError,
    SearchOutcome,
    get_candidates_for_track,
    get_wanted_tracks,
    run_search_for_all_wanted,
    run_search_for_track,
    select_candidate,
)

router = APIRouter(prefix="/api/search", tags=["search"])


class WantedTrackOut(BaseModel):
    id: int
    canonical_artist: str
    featured_artists: list[str]
    canonical_title: str
    parsed_version: str | None
    duration_ms: int
    explicit: bool
    # Which connected playlist(s) actually want this track — denormalized
    # (batched lookup in `wanted_tracks`, not N+1) so the Wanted/Missing page
    # can show it without a second round-trip per row.
    playlist_names: list[str]

    @classmethod
    def from_model(cls, track: Track, playlist_names: list[str]) -> "WantedTrackOut":
        return cls(
            id=track.id,
            canonical_artist=track.canonical_artist,
            featured_artists=track.featured_artists,
            canonical_title=track.canonical_title,
            parsed_version=track.parsed_version,
            duration_ms=track.duration_ms,
            explicit=track.explicit,
            playlist_names=playlist_names,
        )


class VideoCandidateOut(BaseModel):
    id: int
    youtube_video_id: str
    title: str
    channel_id: str | None
    channel_name: str | None
    duration_s: float | None
    orientation: str
    official_signals: dict
    score: int
    score_breakdown: list
    rejection_flags: list

    @classmethod
    def from_model(cls, candidate: VideoCandidate) -> "VideoCandidateOut":
        return cls(
            id=candidate.id,
            youtube_video_id=candidate.youtube_video_id,
            title=candidate.title,
            channel_id=candidate.channel_id,
            channel_name=candidate.channel_name,
            duration_s=candidate.duration_s,
            orientation=candidate.orientation,
            official_signals=candidate.official_signals,
            score=candidate.score,
            score_breakdown=candidate.score_breakdown,
            rejection_flags=candidate.rejection_flags,
        )


class SearchOutcomeOut(BaseModel):
    track_id: int
    media_state: MediaState
    manual_selection: bool
    candidates_found: int
    winning_candidate_id: int | None

    @classmethod
    def from_outcome(cls, track_id: int, outcome: SearchOutcome) -> "SearchOutcomeOut":
        return cls(
            track_id=track_id,
            media_state=outcome.media_asset.state,
            manual_selection=outcome.media_asset.manual_selection,
            candidates_found=outcome.candidates_found,
            winning_candidate_id=outcome.winning_candidate.id if outcome.winning_candidate else None,
        )


class SelectCandidateRequest(BaseModel):
    video_candidate_id: int


async def _get_track_or_404(session: AsyncSession, track_id: int) -> Track:
    track = await session.get(Track, track_id)
    if track is None:
        raise HTTPException(status_code=404, detail=f"No track with id {track_id}")
    return track


async def _load_playlist_names_by_track(session: AsyncSession, track_ids: list[int]) -> dict[int, list[str]]:
    if not track_ids:
        return {}
    result = await session.execute(
        select(PlaylistEntry.track_id, SpotifyPlaylist.name)
        .join(SpotifyPlaylist, SpotifyPlaylist.id == PlaylistEntry.playlist_id)
        .where(PlaylistEntry.track_id.in_(track_ids))
        .distinct()
    )
    names_by_track: dict[int, list[str]] = {tid: [] for tid in track_ids}
    for track_id, name in result.all():
        names_by_track[track_id].append(name)
    return names_by_track


@router.get("/wanted", response_model=list[WantedTrackOut])
async def wanted_tracks(
    page: Pagination = Depends(pagination_params), session: AsyncSession = Depends(get_session)
) -> list[WantedTrackOut]:
    tracks = await get_wanted_tracks(session, limit=page.limit, offset=page.offset)
    names_by_track = await _load_playlist_names_by_track(session, [t.id for t in tracks])
    return [WantedTrackOut.from_model(t, names_by_track.get(t.id, [])) for t in tracks]


@router.post("/run", response_model=list[SearchOutcomeOut])
async def run_all(
    session: AsyncSession = Depends(get_session), http: httpx.AsyncClient = Depends(get_http_client)
) -> list[SearchOutcomeOut]:
    outcomes = await run_search_for_all_wanted(session, http)
    return [SearchOutcomeOut.from_outcome(o.media_asset.track_id, o) for o in outcomes]


@router.post("/tracks/{track_id}/run", response_model=SearchOutcomeOut)
async def run_one(
    track_id: int,
    session: AsyncSession = Depends(get_session),
    http: httpx.AsyncClient = Depends(get_http_client),
) -> SearchOutcomeOut:
    track = await _get_track_or_404(session, track_id)
    outcome = await run_search_for_track(session, http, track)
    return SearchOutcomeOut.from_outcome(track_id, outcome)


@router.post("/tracks/{track_id}/search-again", response_model=SearchOutcomeOut)
async def search_again(
    track_id: int,
    session: AsyncSession = Depends(get_session),
    http: httpx.AsyncClient = Depends(get_http_client),
) -> SearchOutcomeOut:
    track = await _get_track_or_404(session, track_id)
    outcome = await run_search_for_track(session, http, track, reopen=True)
    return SearchOutcomeOut.from_outcome(track_id, outcome)


@router.get("/tracks/{track_id}/candidates", response_model=list[VideoCandidateOut])
async def candidates_for_track(track_id: int, session: AsyncSession = Depends(get_session)) -> list[VideoCandidateOut]:
    await _get_track_or_404(session, track_id)
    candidates = await get_candidates_for_track(session, track_id)
    return [VideoCandidateOut.from_model(c) for c in candidates]


@router.post("/tracks/{track_id}/select", response_model=SearchOutcomeOut)
async def select_candidate_endpoint(
    track_id: int, body: SelectCandidateRequest, session: AsyncSession = Depends(get_session)
) -> SearchOutcomeOut:
    await _get_track_or_404(session, track_id)
    try:
        asset = await select_candidate(session, track_id, body.video_candidate_id)
    except CandidateNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    candidates = await get_candidates_for_track(session, track_id)
    return SearchOutcomeOut(
        track_id=track_id,
        media_state=asset.state,
        manual_selection=asset.manual_selection,
        candidates_found=len(candidates),
        winning_candidate_id=asset.video_candidate_id,
    )


@router.post("/tracks/{track_id}/replace", response_model=SearchOutcomeOut)
async def replace_media_endpoint(
    track_id: int,
    body: SelectCandidateRequest,
    session: AsyncSession = Depends(get_session),
    http: httpx.AsyncClient = Depends(get_http_client),
) -> SearchOutcomeOut:
    """§34: replace an already-downloaded track's file with a different
    chosen candidate. Unlike `/select` (which only applies to a track with
    no file yet), this safely swaps a *working* file: the old one keeps
    serving until the new one is fully downloaded, validated, and imported
    (see app.services.replacement for the exact sequence and why a plain
    Manual Selection can't be reused for this case).
    """
    await _get_track_or_404(session, track_id)
    try:
        new_asset = await replace_track_media(session, http, track_id, body.video_candidate_id)
    except ReplacementError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    candidates = await get_candidates_for_track(session, track_id)
    return SearchOutcomeOut(
        track_id=track_id,
        media_state=new_asset.state,
        manual_selection=new_asset.manual_selection,
        candidates_found=len(candidates),
        winning_candidate_id=new_asset.video_candidate_id,
    )
