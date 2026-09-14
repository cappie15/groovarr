"""Integration tests for the Search API's frontend-wiring additions
(Wanted/Missing + Automatic/Manual Search pages): `WantedTrackOut.playlist_names`
and `MediaAssetOut.video_candidate_id`. Calls route functions directly against
`db_session`, the same way test_media_api.py does, to avoid the SQLite lock
contention that running the full app lifespan across many tests causes.
"""

from app.api.media import list_media_assets
from app.api.pagination import Pagination
from app.api.search import wanted_tracks
from app.db.models.candidates import VideoCandidate
from app.db.models.media import MediaAsset, MediaState
from app.db.models.spotify import PlaylistEntry, SpotifyPlaylist, Track
from app.services.search import get_wanted_tracks


async def test_wanted_tracks_denormalizes_referencing_playlist_names(db_session) -> None:
    track = Track(
        spotify_track_id="t-wanted-1",
        canonical_artist="Justice",
        canonical_title="D.A.N.C.E.",
        duration_ms=200_000,
        explicit=False,
    )
    other_track = Track(
        spotify_track_id="t-wanted-2",
        canonical_artist="Daft Punk",
        canonical_title="Harder, Better, Faster, Stronger",
        duration_ms=224_000,
        explicit=False,
    )
    db_session.add_all([track, other_track])
    await db_session.flush()

    playlist_a = SpotifyPlaylist(spotify_id="pa", name="Gym Mix")
    playlist_b = SpotifyPlaylist(spotify_id="pb", name="French Touch")
    db_session.add_all([playlist_a, playlist_b])
    await db_session.flush()

    # `track` is wanted by both playlists; `other_track` only by playlist_b.
    db_session.add_all(
        [
            PlaylistEntry(playlist_id=playlist_a.id, track_id=track.id, position=0, occurrence_index=0),
            PlaylistEntry(playlist_id=playlist_b.id, track_id=track.id, position=3, occurrence_index=0),
            PlaylistEntry(playlist_id=playlist_b.id, track_id=other_track.id, position=0, occurrence_index=0),
        ]
    )
    await db_session.commit()

    rows = await wanted_tracks(page=Pagination(limit=50, offset=0), session=db_session)
    by_id = {r.id: r for r in rows}

    assert set(by_id[track.id].playlist_names) == {"Gym Mix", "French Touch"}
    assert by_id[other_track.id].playlist_names == ["French Touch"]


async def test_list_media_assets_exposes_video_candidate_id(db_session) -> None:
    track = Track(
        spotify_track_id="t-vc-1",
        canonical_artist="Justice",
        canonical_title="D.A.N.C.E.",
        duration_ms=200_000,
        explicit=False,
    )
    db_session.add(track)
    await db_session.flush()

    candidate = VideoCandidate(
        track_id=track.id,
        youtube_video_id="abc123",
        title="Justice - D.A.N.C.E. (Official Video)",
        score=95,
        score_breakdown=[],
        rejection_flags=[],
        official_signals={},
    )
    db_session.add(candidate)
    await db_session.flush()

    asset = MediaAsset(track_id=track.id, state=MediaState.CANDIDATE_SELECTED, video_candidate_id=candidate.id)
    db_session.add(asset)
    await db_session.commit()

    rows = await list_media_assets(state=None, page=Pagination(limit=50, offset=0), session=db_session)
    [row] = rows
    assert row.video_candidate_id == candidate.id


async def test_list_media_assets_filters_by_for_track_matching_either_track_or_candidate_track(db_session) -> None:
    confirmed_track = Track(
        spotify_track_id="t-ft-1", canonical_artist="A", canonical_title="A Song", duration_ms=180_000, explicit=False
    )
    review_track = Track(
        spotify_track_id="t-ft-2", canonical_artist="B", canonical_title="B Song", duration_ms=180_000, explicit=False
    )
    unrelated_track = Track(
        spotify_track_id="t-ft-3", canonical_artist="C", canonical_title="C Song", duration_ms=180_000, explicit=False
    )
    db_session.add_all([confirmed_track, review_track, unrelated_track])
    await db_session.flush()

    confirmed_asset = MediaAsset(track_id=confirmed_track.id, state=MediaState.AVAILABLE)
    review_asset = MediaAsset(
        track_id=None, candidate_track_id=review_track.id, state=MediaState.MANUAL_REVIEW_REQUIRED
    )
    unrelated_asset = MediaAsset(track_id=unrelated_track.id, state=MediaState.AVAILABLE)
    db_session.add_all([confirmed_asset, review_asset, unrelated_asset])
    await db_session.commit()

    rows = await list_media_assets(
        state=None, for_track=confirmed_track.id, page=Pagination(limit=50, offset=0), session=db_session
    )
    assert [r.id for r in rows] == [confirmed_asset.id]

    rows = await list_media_assets(
        state=None, for_track=review_track.id, page=Pagination(limit=50, offset=0), session=db_session
    )
    assert [r.id for r in rows] == [review_asset.id]


async def test_wanted_tracks_excludes_a_track_already_in_manual_review_via_candidate_track_id(db_session) -> None:
    """Regression test: a MediaAsset created by the existing-library scanner
    (app.services.library_scan) for an uncertain match leaves `track_id`
    null and only sets `candidate_track_id` — `get_wanted_tracks` used to
    only check `track_id`, so such a track incorrectly kept showing up as
    "wanted" even though it was already sitting in Manual Review, and a
    subsequent Automatic Search run (`_get_or_create_media_asset`, keyed on
    `track_id`) would have inserted a second, conflicting MediaAsset for the
    same track instead of finding the pending review row.
    """
    genuinely_wanted = Track(
        spotify_track_id="t-genuinely-wanted",
        canonical_artist="A",
        canonical_title="A Song",
        duration_ms=180_000,
        explicit=False,
    )
    already_in_review = Track(
        spotify_track_id="t-already-in-review",
        canonical_artist="B",
        canonical_title="B Song",
        duration_ms=180_000,
        explicit=False,
    )
    db_session.add_all([genuinely_wanted, already_in_review])
    await db_session.flush()

    playlist = SpotifyPlaylist(spotify_id="p-wanted-regression", name="Regression Playlist")
    db_session.add(playlist)
    await db_session.flush()
    db_session.add_all(
        [
            PlaylistEntry(playlist_id=playlist.id, track_id=genuinely_wanted.id, position=0, occurrence_index=0),
            PlaylistEntry(playlist_id=playlist.id, track_id=already_in_review.id, position=1, occurrence_index=0),
        ]
    )
    # Mirrors library_scan.py's uncertain-match shape exactly.
    db_session.add(
        MediaAsset(
            track_id=None,
            candidate_track_id=already_in_review.id,
            state=MediaState.MANUAL_REVIEW_REQUIRED,
            review_reason="Filename-only match, low confidence.",
        )
    )
    await db_session.commit()

    wanted = await get_wanted_tracks(db_session)
    assert [t.id for t in wanted] == [genuinely_wanted.id]

    # API layer too, exactly as the Wanted page calls it.
    rows = await wanted_tracks(page=Pagination(limit=50, offset=0), session=db_session)
    assert [r.id for r in rows] == [genuinely_wanted.id]
