"""Integration tests for the Spotify playlist sync service — connect, sync
(snapshot_id diffing), and disconnect — against a mocked Spotify backend, no
real network access (§92). These prove the scenarios explicitly required for
Phase 2: initial import, a no-op re-sync, a diff-producing re-sync, duplicate
occurrence preservation, and that disconnect never deletes existing data.
"""

import pytest
from sqlalchemy import select

from app.core.config import get_settings
from app.db.models.media import MediaAsset, MediaState, PlaylistMediaReference
from app.db.models.spotify import PlaylistEntry, SpotifyPlaylist, Track
from app.integrations.spotify.errors import SpotifyAccessDeniedError
from app.services.settings_service import (
    set_spotify_client_credentials,
    set_spotify_user_oauth_enabled,
    store_spotify_user_refresh_token,
)
from app.services.spotify_sync import connect_playlist, disconnect_playlist, sync_playlist
from tests.fixtures.spotify_backend import FakeSpotifyBackend, make_track, playlist_id


async def _configure_credentials(session):
    await set_spotify_client_credentials(session, client_id="test-client-id", client_secret="test-client-secret")


@pytest.fixture
def media_dir(tmp_path, monkeypatch):
    directory = tmp_path / "music-videos"
    directory.mkdir()
    monkeypatch.setenv("MEDIA_DIR", str(directory))
    get_settings.cache_clear()
    yield directory
    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_initial_import_creates_playlist_tracks_and_entries(db_session):
    await _configure_credentials(db_session)
    pid = playlist_id(1)

    backend = FakeSpotifyBackend()
    backend.set_playlist(pid, name="My Playlist", snapshot_id="snap-1")
    backend.set_tracks(
        pid,
        [
            make_track("track-a", "Song A"),
            make_track("track-b", "Song B (Extended Mix)", artists=["Artist B", "Featured One"]),
        ],
    )

    async with backend.build_client() as http:
        playlist = await connect_playlist(db_session, http, f"https://open.spotify.com/playlist/{pid}")

    assert playlist.name == "My Playlist"
    assert playlist.snapshot_id == "snap-1"
    assert playlist.connected is True
    assert playlist.finalized_at is None

    entries = (
        await db_session.execute(
            select(PlaylistEntry).where(PlaylistEntry.playlist_id == playlist.id).order_by(PlaylistEntry.position)
        )
    ).scalars().all()
    assert [e.position for e in entries] == [0, 1]

    track_b = await db_session.scalar(select(Track).where(Track.spotify_track_id == "track-b"))
    assert track_b.canonical_artist == "Artist B"
    assert track_b.featured_artists == ["Featured One"]
    assert track_b.parsed_version == "Extended Mix"
    assert track_b.canonical_title == "Song B"


@pytest.mark.asyncio
async def test_connect_is_idempotent(db_session):
    await _configure_credentials(db_session)
    pid = playlist_id(2)
    backend = FakeSpotifyBackend()
    backend.set_playlist(pid, name="Idempotent", snapshot_id="snap-1")
    backend.set_tracks(pid, [make_track("track-x", "Only Song")])

    async with backend.build_client() as http:
        first = await connect_playlist(db_session, http, pid)
        second = await connect_playlist(db_session, http, pid)

    assert first.id == second.id
    all_playlists = (await db_session.execute(select(SpotifyPlaylist))).scalars().all()
    assert len(all_playlists) == 1


@pytest.mark.asyncio
async def test_resync_with_unchanged_snapshot_is_noop(db_session):
    await _configure_credentials(db_session)
    pid = playlist_id(3)
    backend = FakeSpotifyBackend()
    backend.set_playlist(pid, name="Stable", snapshot_id="snap-1")
    backend.set_tracks(pid, [make_track("track-c", "Song C")])

    async with backend.build_client() as http:
        playlist = await connect_playlist(db_session, http, pid)
        # Not forced, and the backend's snapshot_id hasn't changed — should
        # short-circuit without re-fetching/re-diffing tracks.
        result = await sync_playlist(db_session, http, playlist)

    assert result.unchanged is True
    assert result.tracks_upserted == 0
    assert result.entries_written == 0


@pytest.mark.asyncio
async def test_resync_with_changes_updates_entries_and_order(db_session):
    await _configure_credentials(db_session)
    pid = playlist_id(4)
    backend = FakeSpotifyBackend()
    backend.set_playlist(pid, name="Changing", snapshot_id="snap-1")
    backend.set_tracks(pid, [make_track("track-first", "First"), make_track("track-second", "Second")])

    async with backend.build_client() as http:
        playlist = await connect_playlist(db_session, http, pid)

        # Simulate a Spotify-side change: "Second" removed, "Third" added,
        # remaining tracks reordered.
        backend.set_playlist(pid, name="Changing", snapshot_id="snap-2")
        backend.set_tracks(pid, [make_track("track-third", "Third"), make_track("track-first", "First")])

        result = await sync_playlist(db_session, http, playlist)

    assert result.unchanged is False
    assert playlist.snapshot_id == "snap-2"

    rows = (
        await db_session.execute(
            select(PlaylistEntry, Track)
            .join(Track, Track.id == PlaylistEntry.track_id)
            .where(PlaylistEntry.playlist_id == playlist.id)
            .order_by(PlaylistEntry.position)
        )
    ).all()
    assert [track.canonical_title for _entry, track in rows] == ["Third", "First"]

    # "Second"'s Track row is not deleted just because it left this playlist
    # (§13/§14 — physical/library-level deletion is a separate, reference-
    # counted concern from later phases, not something a sync ever does).
    removed_track = await db_session.scalar(select(Track).where(Track.spotify_track_id == "track-second"))
    assert removed_track is not None


@pytest.mark.asyncio
async def test_duplicate_track_occurrences_are_preserved(db_session):
    await _configure_credentials(db_session)
    pid = playlist_id(5)
    backend = FakeSpotifyBackend()
    backend.set_playlist(pid, name="Dupes", snapshot_id="snap-1")
    backend.set_tracks(
        pid,
        [
            make_track("track-repeat", "Repeat Song"),
            make_track("track-other", "Other Song"),
            make_track("track-repeat", "Repeat Song"),
        ],
    )

    async with backend.build_client() as http:
        playlist = await connect_playlist(db_session, http, pid)

    entries = (
        await db_session.execute(
            select(PlaylistEntry).where(PlaylistEntry.playlist_id == playlist.id).order_by(PlaylistEntry.position)
        )
    ).scalars().all()
    assert len(entries) == 3  # three playlist slots ...

    matching_tracks = (
        await db_session.execute(select(Track).where(Track.spotify_track_id == "track-repeat"))
    ).scalars().all()
    assert len(matching_tracks) == 1  # ... but only one physical Track row (§13)

    repeat_track_id = matching_tracks[0].id
    repeat_entries = [e for e in entries if e.track_id == repeat_track_id]
    assert len(repeat_entries) == 2
    assert {e.occurrence_index for e in repeat_entries} == {0, 1}
    assert {e.position for e in repeat_entries} == {0, 2}


@pytest.mark.asyncio
async def test_disconnect_does_not_delete_existing_data(db_session):
    await _configure_credentials(db_session)
    pid = playlist_id(6)
    backend = FakeSpotifyBackend()
    backend.set_playlist(pid, name="Finished", snapshot_id="snap-1")
    backend.set_tracks(pid, [make_track("track-kept", "Kept Song")])

    async with backend.build_client() as http:
        playlist = await connect_playlist(db_session, http, pid)

    entries_before = (
        await db_session.execute(select(PlaylistEntry).where(PlaylistEntry.playlist_id == playlist.id))
    ).scalars().all()
    assert len(entries_before) == 1

    await disconnect_playlist(db_session, playlist)

    assert playlist.connected is False
    assert playlist.finalized_at is not None

    entries_after = (
        await db_session.execute(select(PlaylistEntry).where(PlaylistEntry.playlist_id == playlist.id))
    ).scalars().all()
    assert [e.id for e in entries_after] == [e.id for e in entries_before]  # completely untouched

    track = await db_session.scalar(select(Track).where(Track.spotify_track_id == "track-kept"))
    assert track is not None


@pytest.mark.asyncio
async def test_resync_removing_a_track_deletes_its_now_unreferenced_media_asset(db_session, media_dir):
    """Phase 9 (§13/§14): when a track that already has a downloaded,
    otherwise-unreferenced MediaAsset disappears from the only playlist that
    wanted it, a routine re-sync must actually clean it up — not just leave
    a permanently-orphaned reference-free asset sitting on disk forever.
    """
    await _configure_credentials(db_session)
    pid = playlist_id(7)
    backend = FakeSpotifyBackend()
    backend.set_playlist(pid, name="Cleanup", snapshot_id="snap-1")
    backend.set_tracks(pid, [make_track("track-gone", "Going Away"), make_track("track-stays", "Staying")])

    async with backend.build_client() as http:
        playlist = await connect_playlist(db_session, http, pid)

    track_gone = await db_session.scalar(select(Track).where(Track.spotify_track_id == "track-gone"))
    video = media_dir / "Artist - Going Away (Unknown) [1080p].mp4"
    video.write_bytes(b"fake video bytes")
    asset = MediaAsset(track_id=track_gone.id, local_path=str(video), state=MediaState.AVAILABLE)
    db_session.add(asset)
    await db_session.flush()
    db_session.add(PlaylistMediaReference(playlist_id=playlist.id, track_id=track_gone.id, media_asset_id=asset.id))
    await db_session.commit()
    asset_id = asset.id

    # Spotify-side change: "Going Away" removed entirely.
    backend.set_playlist(pid, name="Cleanup", snapshot_id="snap-2")
    backend.set_tracks(pid, [make_track("track-stays", "Staying")])
    async with backend.build_client() as http:
        await sync_playlist(db_session, http, playlist)

    assert await db_session.get(MediaAsset, asset_id) is None
    assert not video.exists()
    remaining_refs = (
        await db_session.execute(
            select(PlaylistMediaReference).where(PlaylistMediaReference.track_id == track_gone.id)
        )
    ).scalars().all()
    assert remaining_refs == []


@pytest.mark.asyncio
async def test_resync_removing_a_track_still_referenced_elsewhere_keeps_its_media(db_session, media_dir):
    await _configure_credentials(db_session)
    pid = playlist_id(8)
    backend = FakeSpotifyBackend()
    backend.set_playlist(pid, name="Shared A", snapshot_id="snap-1")
    backend.set_tracks(pid, [make_track("track-shared", "Shared Song")])

    async with backend.build_client() as http:
        playlist_a = await connect_playlist(db_session, http, pid)

    track_shared = await db_session.scalar(select(Track).where(Track.spotify_track_id == "track-shared"))
    video = media_dir / "Artist - Shared Song (Unknown) [1080p].mp4"
    video.write_bytes(b"fake video bytes")
    asset = MediaAsset(track_id=track_shared.id, local_path=str(video), state=MediaState.AVAILABLE)
    db_session.add(asset)
    await db_session.flush()
    db_session.add(
        PlaylistMediaReference(playlist_id=playlist_a.id, track_id=track_shared.id, media_asset_id=asset.id)
    )
    # A second, independent playlist also wants this same track/asset.
    playlist_b = SpotifyPlaylist(spotify_id="other-playlist", name="Shared B", connected=True)
    db_session.add(playlist_b)
    await db_session.flush()
    db_session.add(PlaylistEntry(playlist_id=playlist_b.id, track_id=track_shared.id, position=0, occurrence_index=0))
    db_session.add(
        PlaylistMediaReference(playlist_id=playlist_b.id, track_id=track_shared.id, media_asset_id=asset.id)
    )
    await db_session.commit()

    # Removed from playlist A on Spotify's side — but playlist B (not
    # Spotify-synced in this test, e.g. a manually-scanned/other-sourced
    # reference) still needs it.
    backend.set_playlist(pid, name="Shared A", snapshot_id="snap-2")
    backend.set_tracks(pid, [])
    async with backend.build_client() as http:
        await sync_playlist(db_session, http, playlist_a)

    assert await db_session.get(MediaAsset, asset.id) is not None
    assert video.exists()
    remaining_ref = await db_session.scalar(
        select(PlaylistMediaReference).where(
            PlaylistMediaReference.playlist_id == playlist_a.id, PlaylistMediaReference.track_id == track_shared.id
        )
    )
    assert remaining_ref is None  # playlist A's own reference is gone ...
    other_ref = await db_session.scalar(
        select(PlaylistMediaReference).where(
            PlaylistMediaReference.playlist_id == playlist_b.id, PlaylistMediaReference.track_id == track_shared.id
        )
    )
    assert other_ref is not None  # ... but playlist B's remains, protecting the file


@pytest.mark.asyncio
async def test_resync_adds_reference_when_track_already_has_media_from_another_playlist(db_session, media_dir):
    """A track that already has downloaded media (via some other playlist)
    newly appears in a second, freshly-synced playlist — that playlist must
    get its own PlaylistMediaReference so it "counts" toward keeping the
    file alive and so Phase 8's playlist reconstruction can see it.
    """
    await _configure_credentials(db_session)

    existing_playlist = SpotifyPlaylist(spotify_id="existing", name="Existing", connected=True)
    db_session.add(existing_playlist)
    await db_session.flush()
    track = Track(
        spotify_track_id="track-shared-2", canonical_artist="Artist", canonical_title="Song", duration_ms=200_000
    )
    db_session.add(track)
    await db_session.flush()
    db_session.add(
        PlaylistEntry(playlist_id=existing_playlist.id, track_id=track.id, position=0, occurrence_index=0)
    )
    video = media_dir / "Artist - Song (Unknown) [1080p].mp4"
    video.write_bytes(b"fake video bytes")
    asset = MediaAsset(track_id=track.id, local_path=str(video), state=MediaState.AVAILABLE)
    db_session.add(asset)
    await db_session.flush()
    db_session.add(
        PlaylistMediaReference(playlist_id=existing_playlist.id, track_id=track.id, media_asset_id=asset.id)
    )
    await db_session.commit()

    pid = playlist_id(9)
    backend = FakeSpotifyBackend()
    backend.set_playlist(pid, name="New Playlist", snapshot_id="snap-1")
    backend.set_tracks(pid, [make_track("track-shared-2", "Song")])
    async with backend.build_client() as http:
        new_playlist = await connect_playlist(db_session, http, pid)

    new_ref = await db_session.scalar(
        select(PlaylistMediaReference).where(
            PlaylistMediaReference.playlist_id == new_playlist.id,
            PlaylistMediaReference.track_id == track.id,
            PlaylistMediaReference.media_asset_id == asset.id,
        )
    )
    assert new_ref is not None


@pytest.mark.asyncio
async def test_tracks_endpoint_access_denied_without_pkce_raises_clear_error(db_session):
    """LIVE-VERIFIED (2026-09-14): Spotify's Client Credentials flow can deny
    the track-listing endpoint even for a genuinely public playlist (fetching
    the playlist's own metadata can still succeed). Before this fix, that
    403 on `get_playlist_tracks` was never caught — only the metadata call
    had a PKCE fallback. This proves the fallback now covers both calls, and
    that without PKCE configured, the resulting error is honest (does not
    claim the playlist is "private/collaborative" when it may not be).
    """
    await _configure_credentials(db_session)
    pid = playlist_id(20)

    backend = FakeSpotifyBackend()
    backend.set_playlist(pid, name="Public But Tracks-Denied", snapshot_id="snap-1")
    backend.set_tracks(pid, [make_track("track-x", "Song X")])
    backend.deny_tracks_for_app_token.add(pid)

    async with backend.build_client() as http:
        with pytest.raises(SpotifyAccessDeniedError) as exc_info:
            await connect_playlist(db_session, http, pid)

    message = str(exc_info.value)
    assert "public playlists" in message
    assert "Connect your Spotify account" in message
    # (Rollback-on-error is a real-request behavior, provided by FastAPI's
    # session dependency — already confirmed live via a real HTTP request
    # during this verification pass. The bare `db_session` fixture used here
    # reuses one open, never-rolled-back transaction across the whole test,
    # so it isn't the right place to re-assert that separately.)


@pytest.mark.asyncio
async def test_tracks_endpoint_access_denied_falls_back_to_pkce_user_token(db_session):
    """Same scenario as above, but with the optional PKCE mode configured —
    the fallback must actually retry `get_playlist_tracks` with the user
    token and succeed, not just fall back for the metadata call.
    """
    await _configure_credentials(db_session)
    await set_spotify_user_oauth_enabled(db_session, True)
    await store_spotify_user_refresh_token(db_session, "fake-refresh-token")

    pid = playlist_id(21)
    backend = FakeSpotifyBackend()
    backend.set_playlist(pid, name="Public But Tracks-Denied", snapshot_id="snap-1")
    backend.set_tracks(pid, [make_track("track-y", "Song Y")])
    backend.deny_tracks_for_app_token.add(pid)

    async with backend.build_client() as http:
        playlist = await connect_playlist(db_session, http, pid)

    assert playlist.name == "Public But Tracks-Denied"
    entries = (
        await db_session.execute(select(PlaylistEntry).where(PlaylistEntry.playlist_id == playlist.id))
    ).scalars().all()
    assert len(entries) == 1
