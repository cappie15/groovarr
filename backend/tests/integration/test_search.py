"""Integration tests for Automatic Search / Manual Search / Manual Selection
(§19-27/§33-34/§37). YouTube discovery and yt-dlp enrichment are
monkeypatched at the app.services.search boundary — no real network access
(§92) — proving the orchestration in app/services/search.py against
controlled candidate sets, mirroring how Phase 2 mocked the Spotify API.
"""

import httpx
import pytest
from sqlalchemy import select

from app.db.models.candidates import VideoCandidate
from app.db.models.media import MediaState, PlaylistMediaReference
from app.db.models.spotify import PlaylistEntry, SpotifyPlaylist, Track
from app.domain.reference_counting import is_eligible_for_deletion
from app.integrations.youtube.data_api import RawCandidate
from app.integrations.youtube.ytdlp_client import EnrichedInfo
from app.services.search import (
    CandidateNotFoundError,
    _get_or_create_media_asset,
    get_wanted_tracks,
    run_search_for_track,
    select_candidate,
)


async def _make_wanted_track(session, *, playlist_spotify_id: str = "p1", **overrides) -> Track:
    defaults = dict(
        spotify_track_id="t1",
        canonical_artist="Daft Punk",
        canonical_title="Around the World",
        duration_ms=210_000,
        explicit=False,
    )
    defaults.update(overrides)
    track = Track(**defaults)
    session.add(track)
    await session.flush()

    playlist = SpotifyPlaylist(spotify_id=playlist_spotify_id, name="Playlist")
    session.add(playlist)
    await session.flush()
    session.add(PlaylistEntry(playlist_id=playlist.id, track_id=track.id, position=0, occurrence_index=0))
    await session.commit()
    return track


def _fake_discover(*candidates: RawCandidate):
    async def _discover(session, http, track):
        return list(candidates)

    return _discover


def _fake_enrich(by_video_id: dict[str, EnrichedInfo]):
    async def _enrich(video_id):
        return by_video_id[video_id]

    return _enrich


@pytest.mark.asyncio
async def test_official_candidate_is_selected_automatically(db_session, monkeypatch):
    track = await _make_wanted_track(db_session)
    candidate = RawCandidate(
        youtube_video_id="vid1",
        title="Daft Punk - Around the World (Official Music Video)",
        channel_id="c1",
        channel_name="Daft Punk",
    )
    monkeypatch.setattr("app.services.search.discover_candidates", _fake_discover(candidate))
    monkeypatch.setattr(
        "app.services.search.enrich_candidate",
        _fake_enrich({"vid1": EnrichedInfo(duration_s=209.0, media_type="video", width=1920, height=1080)}),
    )

    async with httpx.AsyncClient() as http:
        outcome = await run_search_for_track(db_session, http, track)

    assert outcome.media_asset.state == MediaState.CANDIDATE_SELECTED
    assert outcome.winning_candidate is not None
    assert outcome.media_asset.video_candidate_id == outcome.winning_candidate.id
    assert outcome.media_asset.source_reference == "youtube:vid1"


@pytest.mark.asyncio
async def test_ambiguous_candidate_goes_to_manual_review_but_is_still_persisted(db_session, monkeypatch):
    track = await _make_wanted_track(db_session)
    candidate = RawCandidate(
        youtube_video_id="vid1",
        title="Cover Band - Around the World (Cover)",
        channel_id="c1",
        channel_name="Random Cover Channel",
    )
    monkeypatch.setattr("app.services.search.discover_candidates", _fake_discover(candidate))
    monkeypatch.setattr(
        "app.services.search.enrich_candidate",
        _fake_enrich({"vid1": EnrichedInfo(duration_s=400.0, media_type="video", width=1920, height=1080)}),
    )

    async with httpx.AsyncClient() as http:
        outcome = await run_search_for_track(db_session, http, track)

    assert outcome.media_asset.state == MediaState.MANUAL_REVIEW_REQUIRED
    assert outcome.media_asset.video_candidate_id is None

    # Manual Search must still be able to show this candidate and its score
    # breakdown (§21/§32), even though it wasn't picked automatically.
    candidates = (await db_session.scalars(select(VideoCandidate).where(VideoCandidate.track_id == track.id))).all()
    assert len(candidates) == 1
    assert candidates[0].score_breakdown  # explainable, not an opaque number


@pytest.mark.asyncio
async def test_shorts_and_portrait_are_hard_excluded_before_scoring(db_session, monkeypatch):
    track = await _make_wanted_track(db_session)
    short_candidate = RawCandidate(
        youtube_video_id="short1", title="Around the World Shorts Edit", channel_id="c1", channel_name="Daft Punk"
    )
    portrait_candidate = RawCandidate(
        youtube_video_id="portrait1",
        title="Daft Punk - Around the World (Official Music Video)",
        channel_id="c1",
        channel_name="Daft Punk",
    )
    monkeypatch.setattr("app.services.search.discover_candidates", _fake_discover(short_candidate, portrait_candidate))
    monkeypatch.setattr(
        "app.services.search.enrich_candidate",
        _fake_enrich(
            {
                "short1": EnrichedInfo(duration_s=15.0, media_type="short", width=1080, height=1920),
                "portrait1": EnrichedInfo(duration_s=210.0, media_type="video", width=1080, height=1920),
            }
        ),
    )

    async with httpx.AsyncClient() as http:
        outcome = await run_search_for_track(db_session, http, track)

    # Both hard-excluded — never scored, never persisted as low-score rows.
    assert outcome.candidates_found == 0
    assert outcome.media_asset.state == MediaState.MISSING
    candidates = (await db_session.scalars(select(VideoCandidate).where(VideoCandidate.track_id == track.id))).all()
    assert candidates == []


@pytest.mark.asyncio
async def test_zero_candidates_marks_missing_without_placeholder(db_session, monkeypatch):
    track = await _make_wanted_track(db_session)
    monkeypatch.setattr("app.services.search.discover_candidates", _fake_discover())
    monkeypatch.setattr("app.services.search.enrich_candidate", _fake_enrich({}))

    async with httpx.AsyncClient() as http:
        outcome = await run_search_for_track(db_session, http, track)

    assert outcome.media_asset.state == MediaState.MISSING
    assert outcome.media_asset.local_path is None  # never a fabricated placeholder (§27)


@pytest.mark.asyncio
async def test_visualizer_only_winner_is_queued_for_download_flagged_not_incomplete_yet(db_session, monkeypatch):
    """A visualizer-only winner still gets *queued for acquisition* like any
    other automatic match (§26 — a visualizer may be acquired when no proper
    music video exists) — it must not be short-circuited straight to
    INCOMPLETE before anything is ever downloaded. The flag that makes Phase
    5's import step land it on INCOMPLETE instead of AVAILABLE is
    `candidate_is_visualizer_only`; see
    tests/integration/test_acquisition.py for the post-download assertion.
    """
    track = await _make_wanted_track(db_session, parsed_version="Extended Mix", duration_ms=360_000)
    candidate = RawCandidate(
        youtube_video_id="vis1",
        title="Daft Punk - Around the World (Extended Mix Visualizer)",
        channel_id="c1",
        channel_name="Daft Punk",
    )
    monkeypatch.setattr("app.services.search.discover_candidates", _fake_discover(candidate))
    monkeypatch.setattr(
        "app.services.search.enrich_candidate",
        _fake_enrich({"vis1": EnrichedInfo(duration_s=359.0, media_type="video", width=1920, height=1080)}),
    )

    async with httpx.AsyncClient() as http:
        outcome = await run_search_for_track(db_session, http, track)

    assert outcome.media_asset.state == MediaState.CANDIDATE_SELECTED
    assert outcome.media_asset.candidate_is_visualizer_only is True
    assert outcome.media_asset.video_candidate_id is not None


@pytest.mark.asyncio
async def test_manual_selection_survives_a_subsequent_automatic_run(db_session, monkeypatch):
    track = await _make_wanted_track(db_session)
    weak_candidate = RawCandidate(
        youtube_video_id="weak1",
        title="Some Channel - Around the World (Cover)",
        channel_id="c1",
        channel_name="Some Channel",
    )
    monkeypatch.setattr("app.services.search.discover_candidates", _fake_discover(weak_candidate))
    monkeypatch.setattr(
        "app.services.search.enrich_candidate",
        _fake_enrich({"weak1": EnrichedInfo(duration_s=210.0, media_type="video", width=1920, height=1080)}),
    )
    async with httpx.AsyncClient() as http:
        await run_search_for_track(db_session, http, track)

    candidates = (await db_session.scalars(select(VideoCandidate).where(VideoCandidate.track_id == track.id))).all()
    chosen = candidates[0]
    await select_candidate(db_session, track.id, chosen.id)

    # A much stronger candidate now "discovered" — a plain automatic re-run
    # (reopen=False, i.e. what the batch "wanted" endpoint would do) must
    # NOT silently override the human's choice (§33/§34).
    strong_candidate = RawCandidate(
        youtube_video_id="strong1",
        title="Daft Punk - Around the World (Official Music Video)",
        channel_id="c1",
        channel_name="Daft Punk",
    )
    monkeypatch.setattr("app.services.search.discover_candidates", _fake_discover(strong_candidate))
    monkeypatch.setattr(
        "app.services.search.enrich_candidate",
        _fake_enrich({"strong1": EnrichedInfo(duration_s=209.0, media_type="video", width=1920, height=1080)}),
    )
    async with httpx.AsyncClient() as http:
        outcome = await run_search_for_track(db_session, http, track)

    assert outcome.media_asset.manual_selection is True
    assert outcome.media_asset.video_candidate_id == chosen.id  # unchanged


@pytest.mark.asyncio
async def test_search_again_can_explicitly_reopen_a_manual_selection(db_session, monkeypatch):
    track = await _make_wanted_track(db_session)
    weak_candidate = RawCandidate(
        youtube_video_id="weak1",
        title="Some Channel - Around the World (Cover)",
        channel_id="c1",
        channel_name="Some Channel",
    )
    monkeypatch.setattr("app.services.search.discover_candidates", _fake_discover(weak_candidate))
    monkeypatch.setattr(
        "app.services.search.enrich_candidate",
        _fake_enrich({"weak1": EnrichedInfo(duration_s=210.0, media_type="video", width=1920, height=1080)}),
    )
    async with httpx.AsyncClient() as http:
        await run_search_for_track(db_session, http, track)
    candidates = (await db_session.scalars(select(VideoCandidate).where(VideoCandidate.track_id == track.id))).all()
    await select_candidate(db_session, track.id, candidates[0].id)

    strong_candidate = RawCandidate(
        youtube_video_id="strong1",
        title="Daft Punk - Around the World (Official Music Video)",
        channel_id="c1",
        channel_name="Daft Punk",
    )
    monkeypatch.setattr("app.services.search.discover_candidates", _fake_discover(strong_candidate))
    monkeypatch.setattr(
        "app.services.search.enrich_candidate",
        _fake_enrich({"strong1": EnrichedInfo(duration_s=209.0, media_type="video", width=1920, height=1080)}),
    )
    async with httpx.AsyncClient() as http:
        outcome = await run_search_for_track(db_session, http, track, reopen=True)

    assert outcome.media_asset.manual_selection is False
    assert outcome.media_asset.state == MediaState.CANDIDATE_SELECTED
    assert outcome.winning_candidate.youtube_video_id == "strong1"


@pytest.mark.asyncio
async def test_wanted_tracks_excludes_tracks_with_an_existing_media_asset(db_session, monkeypatch):
    track = await _make_wanted_track(db_session)
    wanted_before = await get_wanted_tracks(db_session)
    assert track.id in [t.id for t in wanted_before]

    monkeypatch.setattr("app.services.search.discover_candidates", _fake_discover())
    monkeypatch.setattr("app.services.search.enrich_candidate", _fake_enrich({}))
    async with httpx.AsyncClient() as http:
        await run_search_for_track(db_session, http, track)

    wanted_after = await get_wanted_tracks(db_session)
    assert track.id not in [t.id for t in wanted_after]


@pytest.mark.asyncio
async def test_select_candidate_rejects_unknown_candidate_id(db_session):
    track = await _make_wanted_track(db_session)
    with pytest.raises(CandidateNotFoundError):
        await select_candidate(db_session, track.id, 9999)


@pytest.mark.asyncio
async def test_automatic_search_wires_up_playlist_media_reference(db_session, monkeypatch):
    """Phase 9 regression: a fresh Automatic Search match must make the
    resulting MediaAsset visible to reference-counted deletion and Phase 8's
    playlist reconstruction — both of which key on `PlaylistMediaReference`,
    not on `MediaAsset.track_id` alone. Before this fix, only the
    existing-library scanner ever created this row, so a normally
    searched-and-downloaded track's asset had zero references and looked
    instantly "eligible for deletion" despite being actively wanted.
    """
    track = await _make_wanted_track(db_session)
    entry = await db_session.scalar(select(PlaylistEntry).where(PlaylistEntry.track_id == track.id))
    candidate = RawCandidate(
        youtube_video_id="vid1",
        title="Daft Punk - Around the World (Official Music Video)",
        channel_id="c1",
        channel_name="Daft Punk",
    )
    monkeypatch.setattr("app.services.search.discover_candidates", _fake_discover(candidate))
    monkeypatch.setattr(
        "app.services.search.enrich_candidate",
        _fake_enrich({"vid1": EnrichedInfo(duration_s=209.0, media_type="video", width=1920, height=1080)}),
    )

    async with httpx.AsyncClient() as http:
        outcome = await run_search_for_track(db_session, http, track)

    ref = await db_session.scalar(
        select(PlaylistMediaReference).where(
            PlaylistMediaReference.playlist_id == entry.playlist_id,
            PlaylistMediaReference.track_id == track.id,
            PlaylistMediaReference.media_asset_id == outcome.media_asset.id,
        )
    )
    assert ref is not None
    assert await is_eligible_for_deletion(db_session, outcome.media_asset.id) is False


@pytest.mark.asyncio
async def test_reopen_on_an_already_downloaded_asset_never_mutates_it_directly(db_session, monkeypatch):
    """Phase 9 (§36/§37): re-searching a track that already has a real,
    currently-serving file (Available or Incomplete) must never flip its
    state/video_candidate_id/local_path itself — only
    app.services.replacement.replace_track_media may do that, via a safe
    swap. This function stays a pure "discover, score, report" operation for
    an already-downloaded track; the winning candidate is still returned for
    a caller (the upgrade monitor) to act on explicitly.
    """
    track = await _make_wanted_track(db_session)
    asset = await _get_or_create_media_asset(db_session, track.id)
    asset.local_path = "/music-videos/Artist - Title (Unknown) [1080p].mp4"
    asset.state = MediaState.AVAILABLE
    asset.video_candidate_id = None
    asset.source_reference = "youtube:old-video"
    await db_session.commit()

    candidate = RawCandidate(
        youtube_video_id="new-and-better",
        title="Daft Punk - Around the World (Official Music Video)",
        channel_id="c1",
        channel_name="Daft Punk",
    )
    monkeypatch.setattr("app.services.search.discover_candidates", _fake_discover(candidate))
    monkeypatch.setattr(
        "app.services.search.enrich_candidate",
        _fake_enrich({"new-and-better": EnrichedInfo(duration_s=209.0, media_type="video", width=1920, height=1080)}),
    )

    async with httpx.AsyncClient() as http:
        outcome = await run_search_for_track(db_session, http, track, reopen=True)

    assert outcome.winning_candidate is not None
    assert outcome.winning_candidate.youtube_video_id == "new-and-better"
    # ... but the actual asset is completely untouched.
    assert asset.state == MediaState.AVAILABLE
    assert asset.local_path == "/music-videos/Artist - Title (Unknown) [1080p].mp4"
    assert asset.source_reference == "youtube:old-video"


@pytest.mark.asyncio
async def test_reopen_on_an_already_downloaded_asset_with_zero_candidates_stays_available(db_session, monkeypatch):
    track = await _make_wanted_track(db_session)
    asset = await _get_or_create_media_asset(db_session, track.id)
    asset.local_path = "/music-videos/Artist - Title (Unknown) [1080p].mp4"
    asset.state = MediaState.INCOMPLETE
    await db_session.commit()

    monkeypatch.setattr("app.services.search.discover_candidates", _fake_discover())
    monkeypatch.setattr("app.services.search.enrich_candidate", _fake_enrich({}))

    async with httpx.AsyncClient() as http:
        outcome = await run_search_for_track(db_session, http, track, reopen=True)

    assert outcome.candidates_found == 0
    assert asset.state == MediaState.INCOMPLETE  # never demoted to MISSING
    assert asset.local_path is not None


@pytest.mark.asyncio
async def test_manual_selection_also_wires_up_playlist_media_reference(db_session, monkeypatch):
    track = await _make_wanted_track(db_session)
    entry = await db_session.scalar(select(PlaylistEntry).where(PlaylistEntry.track_id == track.id))
    candidate = RawCandidate(
        youtube_video_id="weak1", title="Some Channel - Around the World (Cover)", channel_id="c1", channel_name="X"
    )
    monkeypatch.setattr("app.services.search.discover_candidates", _fake_discover(candidate))
    monkeypatch.setattr(
        "app.services.search.enrich_candidate",
        _fake_enrich({"weak1": EnrichedInfo(duration_s=210.0, media_type="video", width=1920, height=1080)}),
    )
    async with httpx.AsyncClient() as http:
        await run_search_for_track(db_session, http, track)
    candidates = (await db_session.scalars(select(VideoCandidate).where(VideoCandidate.track_id == track.id))).all()

    asset = await select_candidate(db_session, track.id, candidates[0].id)

    ref = await db_session.scalar(
        select(PlaylistMediaReference).where(
            PlaylistMediaReference.playlist_id == entry.playlist_id,
            PlaylistMediaReference.track_id == track.id,
            PlaylistMediaReference.media_asset_id == asset.id,
        )
    )
    assert ref is not None


@pytest.mark.asyncio
async def test_bulk_sweep_paces_between_tracks_but_not_before_the_first(db_session, monkeypatch):
    """`run_search_for_all_wanted` must pause between tracks (§ rationale in
    app.services.search._pace_bulk_search: a real YouTube 403 this project
    hit, mitigated by cooperative pacing rather than proxy rotation) but
    never before the very first track — an N-track sweep should pace
    exactly N-1 times, not N.
    """
    from app.services import search as search_module

    track_a = await _make_wanted_track(
        db_session, playlist_spotify_id="pa", spotify_track_id="ta", canonical_title="Track A"
    )
    track_b = await _make_wanted_track(
        db_session, playlist_spotify_id="pb", spotify_track_id="tb", canonical_title="Track B"
    )
    track_c = await _make_wanted_track(
        db_session, playlist_spotify_id="pc", spotify_track_id="tc", canonical_title="Track C"
    )

    monkeypatch.setattr("app.services.search.discover_candidates", _fake_discover())
    monkeypatch.setattr("app.services.search.enrich_candidate", _fake_enrich({}))

    pace_calls = 0

    async def _fast_pace() -> None:
        nonlocal pace_calls
        pace_calls += 1

    monkeypatch.setattr(search_module, "_pace_bulk_search", _fast_pace)

    async with httpx.AsyncClient() as http:
        outcomes = await search_module.run_search_for_all_wanted(db_session, http)

    assert {track_a.id, track_b.id, track_c.id} == {o.media_asset.track_id for o in outcomes}
    assert pace_calls == 2  # 3 tracks -> paced between them, not before the first


@pytest.mark.asyncio
async def test_single_track_search_is_never_paced(db_session, monkeypatch):
    """A single user-initiated search (Manual/Automatic Search UI for one
    track) must stay instant — pacing is specifically about avoiding a
    burst across MANY tracks, not slowing down one explicit action.
    """
    from app.services import search as search_module

    track = await _make_wanted_track(db_session)
    monkeypatch.setattr("app.services.search.discover_candidates", _fake_discover())
    monkeypatch.setattr("app.services.search.enrich_candidate", _fake_enrich({}))

    pace_calls = 0

    async def _counting_pace() -> None:
        nonlocal pace_calls
        pace_calls += 1

    monkeypatch.setattr(search_module, "_pace_bulk_search", _counting_pace)

    async with httpx.AsyncClient() as http:
        await run_search_for_track(db_session, http, track)

    assert pace_calls == 0
