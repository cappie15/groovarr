"""Reference-counting eligibility tests (§13/§14/§70) — see
app/domain/reference_counting.py. Proves the specific case the spec calls
out explicitly: finalizing/disconnecting a playlist must NOT make its media
garbage-collectable, because finalizing never removes PlaylistMediaReference
rows.
"""

import pytest

from app.db.models.media import MediaAsset, MediaState, PlaylistMediaReference
from app.db.models.spotify import PlaylistEntry, SpotifyPlaylist, Track
from app.domain.reference_counting import is_eligible_for_deletion, reference_count
from app.services.spotify_sync import disconnect_playlist


async def _make_track(session, spotify_track_id: str) -> Track:
    track = Track(
        spotify_track_id=spotify_track_id,
        canonical_artist="Artist",
        canonical_title="Title",
        duration_ms=200_000,
    )
    session.add(track)
    await session.flush()
    return track


async def _make_playlist(session, spotify_id: str) -> SpotifyPlaylist:
    playlist = SpotifyPlaylist(spotify_id=spotify_id, name="Playlist", connected=True)
    session.add(playlist)
    await session.flush()
    return playlist


@pytest.mark.asyncio
async def test_zero_references_is_eligible(db_session):
    track = await _make_track(db_session, "t1")
    asset = MediaAsset(track_id=track.id, local_path="/music-videos/a.mp4", state=MediaState.AVAILABLE)
    db_session.add(asset)
    await db_session.commit()

    assert await reference_count(db_session, asset.id) == 0
    assert await is_eligible_for_deletion(db_session, asset.id) is True


@pytest.mark.asyncio
async def test_active_playlist_reference_makes_it_ineligible(db_session):
    track = await _make_track(db_session, "t2")
    playlist = await _make_playlist(db_session, "p1")
    asset = MediaAsset(track_id=track.id, local_path="/music-videos/b.mp4", state=MediaState.AVAILABLE)
    db_session.add(asset)
    await db_session.flush()

    db_session.add(PlaylistEntry(playlist_id=playlist.id, track_id=track.id, position=0, occurrence_index=0))
    db_session.add(PlaylistMediaReference(playlist_id=playlist.id, track_id=track.id, media_asset_id=asset.id))
    await db_session.commit()

    assert await reference_count(db_session, asset.id) == 1
    assert await is_eligible_for_deletion(db_session, asset.id) is False


@pytest.mark.asyncio
async def test_finalized_playlist_reference_still_makes_it_ineligible(db_session):
    """The specific case §10/§14 calls out: disconnecting/finalizing a
    playlist must not make its media garbage-collectable.
    """
    track = await _make_track(db_session, "t3")
    playlist = await _make_playlist(db_session, "p2")
    asset = MediaAsset(track_id=track.id, local_path="/music-videos/c.mp4", state=MediaState.AVAILABLE)
    db_session.add(asset)
    await db_session.flush()

    db_session.add(PlaylistEntry(playlist_id=playlist.id, track_id=track.id, position=0, occurrence_index=0))
    db_session.add(PlaylistMediaReference(playlist_id=playlist.id, track_id=track.id, media_asset_id=asset.id))
    await db_session.commit()

    await disconnect_playlist(db_session, playlist)
    assert playlist.finalized_at is not None

    # The reference row must still be there, untouched by finalization.
    assert await reference_count(db_session, asset.id) == 1
    assert await is_eligible_for_deletion(db_session, asset.id) is False


@pytest.mark.asyncio
async def test_duplicate_occurrences_share_one_reference_row(db_session):
    """Two PlaylistEntry rows (duplicate occurrences of the same track in
    one playlist, §12) must not inflate the reference count beyond 1 — the
    physical file is referenced once by that playlist regardless of how many
    times the track appears in it (§13).
    """
    track = await _make_track(db_session, "t4")
    playlist = await _make_playlist(db_session, "p3")
    asset = MediaAsset(track_id=track.id, local_path="/music-videos/d.mp4", state=MediaState.AVAILABLE)
    db_session.add(asset)
    await db_session.flush()

    db_session.add(PlaylistEntry(playlist_id=playlist.id, track_id=track.id, position=0, occurrence_index=0))
    db_session.add(PlaylistEntry(playlist_id=playlist.id, track_id=track.id, position=5, occurrence_index=1))
    db_session.add(PlaylistMediaReference(playlist_id=playlist.id, track_id=track.id, media_asset_id=asset.id))
    await db_session.commit()

    assert await reference_count(db_session, asset.id) == 1
