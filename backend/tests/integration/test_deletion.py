"""Real reference-counted deletion (§13/§14/§70) — see
app/services/deletion.py. Builds on app/domain/reference_counting.py's
eligibility predicate (already tested in test_reference_counting.py) to
prove the actual destructive action: only fires when truly zero-referenced,
cleans up the `.lrc` sidecar too, and refuses anything outside the §70
filesystem-safety checklist.
"""

import pytest

from app.core.config import get_settings
from app.db.models.media import MediaAsset, MediaState, PlaylistMediaReference
from app.db.models.spotify import SpotifyPlaylist, Track
from app.services.deletion import DeletionRefusedError, delete_media_asset_if_eligible
from app.services.spotify_sync import disconnect_playlist


@pytest.fixture
def media_dir(tmp_path, monkeypatch):
    directory = tmp_path / "music-videos"
    directory.mkdir()
    monkeypatch.setenv("MEDIA_DIR", str(directory))
    get_settings.cache_clear()
    yield directory
    get_settings.cache_clear()


async def _make_track(session, spotify_track_id: str = "t1") -> Track:
    track = Track(
        spotify_track_id=spotify_track_id, canonical_artist="Artist", canonical_title="Title", duration_ms=200_000
    )
    session.add(track)
    await session.flush()
    return track


async def _make_playlist(session, spotify_id: str = "p1") -> SpotifyPlaylist:
    playlist = SpotifyPlaylist(spotify_id=spotify_id, name="Playlist", connected=True)
    session.add(playlist)
    await session.flush()
    return playlist


@pytest.mark.asyncio
async def test_deletes_file_and_sidecar_when_zero_referenced(db_session, media_dir):
    track = await _make_track(db_session)
    video = media_dir / "Artist - Title (Unknown) [1080p].mp4"
    video.write_bytes(b"fake video bytes")
    sidecar = video.with_suffix(".lrc")
    sidecar.write_text("[00:01.00]la la la")

    asset = MediaAsset(track_id=track.id, local_path=str(video), state=MediaState.AVAILABLE)
    db_session.add(asset)
    await db_session.commit()
    asset_id = asset.id

    deleted = await delete_media_asset_if_eligible(db_session, asset)

    assert deleted is True
    assert not video.exists()
    assert not sidecar.exists()
    assert await db_session.get(MediaAsset, asset_id) is None


@pytest.mark.asyncio
async def test_refuses_when_still_referenced_by_an_active_playlist(db_session, media_dir):
    track = await _make_track(db_session)
    playlist = await _make_playlist(db_session)
    video = media_dir / "Artist - Title (Unknown) [1080p].mp4"
    video.write_bytes(b"fake video bytes")

    asset = MediaAsset(track_id=track.id, local_path=str(video), state=MediaState.AVAILABLE)
    db_session.add(asset)
    await db_session.flush()
    db_session.add(PlaylistMediaReference(playlist_id=playlist.id, track_id=track.id, media_asset_id=asset.id))
    await db_session.commit()

    deleted = await delete_media_asset_if_eligible(db_session, asset)

    assert deleted is False
    assert video.exists()
    assert await db_session.get(MediaAsset, asset.id) is not None


@pytest.mark.asyncio
async def test_refuses_when_referenced_only_by_a_finalized_playlist(db_session, media_dir):
    """The exact case §10/§14 call out: a finalized/disconnected playlist's
    reference must still protect its media from deletion.
    """
    track = await _make_track(db_session)
    playlist = await _make_playlist(db_session)
    video = media_dir / "Artist - Title (Unknown) [1080p].mp4"
    video.write_bytes(b"fake video bytes")

    asset = MediaAsset(track_id=track.id, local_path=str(video), state=MediaState.AVAILABLE)
    db_session.add(asset)
    await db_session.flush()
    db_session.add(PlaylistMediaReference(playlist_id=playlist.id, track_id=track.id, media_asset_id=asset.id))
    await db_session.commit()

    await disconnect_playlist(db_session, playlist)

    deleted = await delete_media_asset_if_eligible(db_session, asset)

    assert deleted is False
    assert video.exists()


@pytest.mark.asyncio
async def test_second_active_playlist_still_protects_after_first_is_removed(db_session, media_dir):
    track = await _make_track(db_session)
    playlist_a = await _make_playlist(db_session, "pa")
    playlist_b = await _make_playlist(db_session, "pb")
    video = media_dir / "Artist - Title (Unknown) [1080p].mp4"
    video.write_bytes(b"fake video bytes")

    asset = MediaAsset(track_id=track.id, local_path=str(video), state=MediaState.AVAILABLE)
    db_session.add(asset)
    await db_session.flush()
    ref_a = PlaylistMediaReference(playlist_id=playlist_a.id, track_id=track.id, media_asset_id=asset.id)
    db_session.add(ref_a)
    db_session.add(PlaylistMediaReference(playlist_id=playlist_b.id, track_id=track.id, media_asset_id=asset.id))
    await db_session.commit()

    # Playlist A's reference goes away (as spotify_sync would do when the
    # track is removed from playlist A) — playlist B still wants it.
    await db_session.delete(ref_a)
    await db_session.commit()

    deleted = await delete_media_asset_if_eligible(db_session, asset)

    assert deleted is False
    assert video.exists()


@pytest.mark.asyncio
async def test_refuses_path_outside_media_root(db_session, media_dir, tmp_path):
    track = await _make_track(db_session)
    outside = tmp_path / "outside.mp4"
    outside.write_bytes(b"bytes")

    asset = MediaAsset(track_id=track.id, local_path=str(outside), state=MediaState.AVAILABLE)
    db_session.add(asset)
    await db_session.commit()

    with pytest.raises(DeletionRefusedError, match="media root"):
        await delete_media_asset_if_eligible(db_session, asset)

    assert outside.exists()
    assert await db_session.get(MediaAsset, asset.id) is not None


@pytest.mark.asyncio
async def test_refuses_directory_target(db_session, media_dir):
    track = await _make_track(db_session)
    directory = media_dir / "not-a-file"
    directory.mkdir()

    asset = MediaAsset(track_id=track.id, local_path=str(directory), state=MediaState.AVAILABLE)
    db_session.add(asset)
    await db_session.commit()

    with pytest.raises(DeletionRefusedError, match="regular-file"):
        await delete_media_asset_if_eligible(db_session, asset)

    assert directory.exists()


@pytest.mark.asyncio
async def test_asset_with_no_local_path_is_just_dropped(db_session, media_dir):
    """A MANUAL_REVIEW_REQUIRED/SEARCHING asset that never reached a
    downloaded file has nothing to unlink — deletion should still succeed,
    just skipping the filesystem step.
    """
    track = await _make_track(db_session)
    asset = MediaAsset(candidate_track_id=track.id, state=MediaState.MANUAL_REVIEW_REQUIRED, review_reason="x")
    db_session.add(asset)
    await db_session.commit()
    asset_id = asset.id

    deleted = await delete_media_asset_if_eligible(db_session, asset)

    assert deleted is True
    assert await db_session.get(MediaAsset, asset_id) is None
