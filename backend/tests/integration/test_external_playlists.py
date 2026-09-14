"""Integration tests for playlist reconstruction and per-asset media-server
sync (Phase 8, §53-61) against faked Jellyfin/Plex backends (§92) — no live
server, no real network access.

Covers: Spotify-order preservation with full duplicate preservation on
Jellyfin, Plex's own duplicate-collapse recorded honestly, a Missing track
omitted (no placeholder) and appearing once acquired, name-collision
detection routing to NEEDS_RESOLUTION instead of silently overwriting, the
adopt resolution path, and the critical "external server errors" case
leaving `MediaAsset.state` completely untouched.
"""

import pytest
from sqlalchemy import select

from app.db.models.external_playlist import ExternalPlatform, ExternalPlaylist, ExternalPlaylistSyncState
from app.db.models.media import MediaAsset, MediaState, PlaylistMediaReference, SyncStatus
from app.db.models.spotify import PlaylistEntry, SpotifyPlaylist, Track
from app.services.external_playlists import sync_external_playlist, sync_media_servers_for_asset
from app.services.settings_service import set_jellyfin_config, set_plex_config
from tests.fixtures.jellyfin_backend import FakeJellyfinBackend
from tests.fixtures.plex_backend import FakePlexBackend


async def _configure_jellyfin(session, *, user_id: str = "user-1", media_path: str | None = None) -> None:
    await set_jellyfin_config(
        session,
        enabled=True,
        url="http://jellyfin.local",
        api_key="fake-key",
        user_id=user_id,
        library_id=None,
        media_path=media_path,
    )


async def _configure_plex(session, *, section_id: str = "1") -> None:
    await set_plex_config(
        session,
        enabled=True,
        url="http://plex.local",
        token="fake-token",
        library_section_id=section_id,
        media_path=None,
    )


async def _make_playlist(
    session, *, name: str = "Test Playlist", jellyfin_enabled=False, plex_enabled=False
) -> SpotifyPlaylist:
    playlist = SpotifyPlaylist(
        spotify_id=f"pid-{name}", name=name, jellyfin_enabled=jellyfin_enabled, plex_enabled=plex_enabled
    )
    session.add(playlist)
    await session.flush()
    return playlist


async def _make_track(session, *, spotify_track_id: str, title: str) -> Track:
    track = Track(
        spotify_track_id=spotify_track_id,
        canonical_artist="Test Artist",
        featured_artists=[],
        canonical_title=title,
        duration_ms=200_000,
        explicit=False,
    )
    session.add(track)
    await session.flush()
    return track


async def _make_available_asset(session, *, track: Track, local_path: str) -> MediaAsset:
    asset = MediaAsset(track_id=track.id, local_path=local_path, state=MediaState.AVAILABLE)
    session.add(asset)
    await session.flush()
    return asset


async def _link(session, *, playlist: SpotifyPlaylist, track: Track, asset: MediaAsset) -> None:
    session.add(PlaylistMediaReference(playlist_id=playlist.id, track_id=track.id, media_asset_id=asset.id))
    await session.flush()


@pytest.mark.asyncio
async def test_jellyfin_preserves_full_duplicate_occurrences_in_spotify_order(db_session):
    await _configure_jellyfin(db_session)
    playlist = await _make_playlist(db_session, jellyfin_enabled=True)

    track_a = await _make_track(db_session, spotify_track_id="a", title="Song A")
    track_b = await _make_track(db_session, spotify_track_id="b", title="Song B")

    backend = FakeJellyfinBackend()
    item_a = backend.add_item(name="Song A", path="/music-videos/a.mp4")
    item_b = backend.add_item(name="Song B", path="/music-videos/b.mp4")

    asset_a = await _make_available_asset(db_session, track=track_a, local_path="/music-videos/a.mp4")
    asset_b = await _make_available_asset(db_session, track=track_b, local_path="/music-videos/b.mp4")
    await _link(db_session, playlist=playlist, track=track_a, asset=asset_a)
    await _link(db_session, playlist=playlist, track=track_b, asset=asset_b)

    # A at position 0 and 2 (duplicate occurrence), B at position 1 — per §12.
    db_session.add_all(
        [
            PlaylistEntry(playlist_id=playlist.id, track_id=track_a.id, position=0, occurrence_index=0),
            PlaylistEntry(playlist_id=playlist.id, track_id=track_b.id, position=1, occurrence_index=0),
            PlaylistEntry(playlist_id=playlist.id, track_id=track_a.id, position=2, occurrence_index=1),
        ]
    )
    await db_session.commit()

    async with backend.build_client() as http:
        outcome = await sync_external_playlist(db_session, http, ExternalPlatform.JELLYFIN, playlist)

    assert outcome.state == ExternalPlaylistSyncState.SYNCED
    assert outcome.item_count == 3  # duplicate preserved logically

    row = await db_session.scalar(
        select(ExternalPlaylist).where(
            ExternalPlaylist.platform == ExternalPlatform.JELLYFIN, ExternalPlaylist.spotify_playlist_id == playlist.id
        )
    )
    created = backend.playlists[row.external_id]
    assert created["_items"] == [item_a, item_b, item_a]  # order AND duplicate both preserved


@pytest.mark.asyncio
async def test_plex_collapses_duplicates_and_records_the_count(db_session):
    await _configure_plex(db_session)
    playlist = await _make_playlist(db_session, plex_enabled=True)
    track_a = await _make_track(db_session, spotify_track_id="a", title="Song A")

    backend = FakePlexBackend()
    backend.add_item("1", title="Song A", file_path="/music-videos/a.mp4")

    asset_a = await _make_available_asset(db_session, track=track_a, local_path="/music-videos/a.mp4")
    await _link(db_session, playlist=playlist, track=track_a, asset=asset_a)
    db_session.add_all(
        [
            PlaylistEntry(playlist_id=playlist.id, track_id=track_a.id, position=0, occurrence_index=0),
            PlaylistEntry(playlist_id=playlist.id, track_id=track_a.id, position=1, occurrence_index=1),
        ]
    )
    await db_session.commit()

    async with backend.build_client() as http:
        outcome = await sync_external_playlist(db_session, http, ExternalPlatform.PLEX, playlist)

    assert outcome.item_count == 2  # logical (pre-collapse) count
    assert outcome.duplicates_collapsed == 1  # Plex's own server-side dedup collapsed one

    row = await db_session.scalar(
        select(ExternalPlaylist).where(
            ExternalPlaylist.platform == ExternalPlatform.PLEX, ExternalPlaylist.spotify_playlist_id == playlist.id
        )
    )
    assert row.duplicates_collapsed_count == 1


@pytest.mark.asyncio
async def test_missing_track_is_omitted_then_appears_once_acquired(db_session):
    await _configure_jellyfin(db_session)
    playlist = await _make_playlist(db_session, jellyfin_enabled=True)

    track_a = await _make_track(db_session, spotify_track_id="a", title="Song A")
    track_missing = await _make_track(db_session, spotify_track_id="m", title="Song Missing")
    track_b = await _make_track(db_session, spotify_track_id="b", title="Song B")

    backend = FakeJellyfinBackend()
    item_a = backend.add_item(name="Song A", path="/music-videos/a.mp4")
    item_b = backend.add_item(name="Song B", path="/music-videos/b.mp4")

    asset_a = await _make_available_asset(db_session, track=track_a, local_path="/music-videos/a.mp4")
    asset_b = await _make_available_asset(db_session, track=track_b, local_path="/music-videos/b.mp4")
    await _link(db_session, playlist=playlist, track=track_a, asset=asset_a)
    await _link(db_session, playlist=playlist, track=track_b, asset=asset_b)
    # track_missing has no MediaAsset/reference at all — still WANTED.

    db_session.add_all(
        [
            PlaylistEntry(playlist_id=playlist.id, track_id=track_a.id, position=0, occurrence_index=0),
            PlaylistEntry(playlist_id=playlist.id, track_id=track_missing.id, position=1, occurrence_index=0),
            PlaylistEntry(playlist_id=playlist.id, track_id=track_b.id, position=2, occurrence_index=0),
        ]
    )
    await db_session.commit()

    async with backend.build_client() as http:
        outcome = await sync_external_playlist(db_session, http, ExternalPlatform.JELLYFIN, playlist)
    assert outcome.item_count == 2  # missing track simply omitted — no placeholder

    row = await db_session.scalar(
        select(ExternalPlaylist).where(
            ExternalPlaylist.platform == ExternalPlatform.JELLYFIN, ExternalPlaylist.spotify_playlist_id == playlist.id
        )
    )
    assert backend.playlists[row.external_id]["_items"] == [item_a, item_b]

    # Now the missing track gets acquired.
    item_missing = backend.add_item(name="Song Missing", path="/music-videos/m.mp4")
    asset_missing = await _make_available_asset(db_session, track=track_missing, local_path="/music-videos/m.mp4")
    await _link(db_session, playlist=playlist, track=track_missing, asset=asset_missing)
    await db_session.commit()

    async with backend.build_client() as http:
        outcome2 = await sync_external_playlist(db_session, http, ExternalPlatform.JELLYFIN, playlist)
    assert outcome2.item_count == 3

    await db_session.refresh(row)
    assert backend.playlists[row.external_id]["_items"] == [item_a, item_missing, item_b]  # correct position


@pytest.mark.asyncio
async def test_name_collision_routes_to_needs_resolution_without_overwriting(db_session):
    await _configure_jellyfin(db_session)
    playlist = await _make_playlist(db_session, name="Collides", jellyfin_enabled=True)
    track_a = await _make_track(db_session, spotify_track_id="a", title="Song A")

    backend = FakeJellyfinBackend()
    backend.add_item(name="Song A", path="/music-videos/a.mp4")
    # A pre-existing, Groovarr-unmanaged playlist under the same name.
    backend.playlists["foreign-1"] = {"Id": "foreign-1", "Name": "Collides", "_items": []}

    asset_a = await _make_available_asset(db_session, track=track_a, local_path="/music-videos/a.mp4")
    await _link(db_session, playlist=playlist, track=track_a, asset=asset_a)
    db_session.add(PlaylistEntry(playlist_id=playlist.id, track_id=track_a.id, position=0, occurrence_index=0))
    await db_session.commit()

    async with backend.build_client() as http:
        outcome = await sync_external_playlist(db_session, http, ExternalPlatform.JELLYFIN, playlist)

    assert outcome.state == ExternalPlaylistSyncState.NEEDS_RESOLUTION
    assert len(backend.playlists) == 1  # nothing new created
    assert backend.playlists["foreign-1"]["_items"] == []  # untouched


@pytest.mark.asyncio
async def test_adopt_resolution_lets_a_future_sync_take_over(db_session):
    await _configure_jellyfin(db_session)
    playlist = await _make_playlist(db_session, name="Collides", jellyfin_enabled=True)
    track_a = await _make_track(db_session, spotify_track_id="a", title="Song A")

    backend = FakeJellyfinBackend()
    item_a = backend.add_item(name="Song A", path="/music-videos/a.mp4")
    backend.playlists["foreign-1"] = {"Id": "foreign-1", "Name": "Collides", "_items": []}

    asset_a = await _make_available_asset(db_session, track=track_a, local_path="/music-videos/a.mp4")
    await _link(db_session, playlist=playlist, track=track_a, asset=asset_a)
    db_session.add(PlaylistEntry(playlist_id=playlist.id, track_id=track_a.id, position=0, occurrence_index=0))
    await db_session.commit()

    async with backend.build_client() as http:
        await sync_external_playlist(db_session, http, ExternalPlatform.JELLYFIN, playlist)

        row = await db_session.scalar(
            select(ExternalPlaylist).where(
                ExternalPlaylist.platform == ExternalPlatform.JELLYFIN,
                ExternalPlaylist.spotify_playlist_id == playlist.id,
            )
        )
        assert row.sync_state == ExternalPlaylistSyncState.NEEDS_RESOLUTION

        from app.services.external_playlists import resolve_playlist_collision

        row = await resolve_playlist_collision(db_session, row, action="adopt")
        assert row.adopted is True

        outcome = await sync_external_playlist(db_session, http, ExternalPlatform.JELLYFIN, playlist)

    assert outcome.state == ExternalPlaylistSyncState.SYNCED
    # Adopting rebuilds content via delete+recreate — the foreign id is gone,
    # replaced by a fresh playlist with the correct content.
    assert "foreign-1" not in backend.playlists
    new_ids = [pid for pid in backend.playlists if pid != "foreign-1"]
    assert backend.playlists[new_ids[0]]["_items"] == [item_a]


@pytest.mark.asyncio
async def test_media_server_failure_never_touches_media_state(db_session):
    """The critical §61 guarantee: a Jellyfin/Plex error must only ever
    affect `jellyfin_sync_status`/`plex_sync_status`, never `MediaAsset.state`.
    """
    import httpx

    await _configure_jellyfin(db_session)
    playlist = await _make_playlist(db_session, jellyfin_enabled=True)
    track_a = await _make_track(db_session, spotify_track_id="a", title="Song A")
    asset_a = await _make_available_asset(db_session, track=track_a, local_path="/music-videos/a.mp4")
    await _link(db_session, playlist=playlist, track=track_a, asset=asset_a)
    db_session.add(PlaylistEntry(playlist_id=playlist.id, track_id=track_a.id, position=0, occurrence_index=0))
    await db_session.commit()

    def _boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("simulated Jellyfin outage", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(_boom)) as http:
        await sync_media_servers_for_asset(db_session, http, asset_a)

    await db_session.refresh(asset_a)
    assert asset_a.state == MediaState.AVAILABLE  # completely untouched
    assert asset_a.jellyfin_sync_status == SyncStatus.FAILED_SYNC  # only the sync-status field reflects it


@pytest.mark.asyncio
async def test_jellyfin_no_remap_configured_matches_identical_path(db_session):
    """Regression baseline: with no `jellyfin_media_path` configured (the
    default), item lookup by exact path must behave exactly as it did before
    the remap parity fix — Groovarr's own local path is used as-is.
    """
    await _configure_jellyfin(db_session)  # media_path=None (default)
    playlist = await _make_playlist(db_session, jellyfin_enabled=True)
    track_a = await _make_track(db_session, spotify_track_id="a", title="Song A")
    asset_a = await _make_available_asset(db_session, track=track_a, local_path="/music-videos/a.mp4")
    await _link(db_session, playlist=playlist, track=track_a, asset=asset_a)
    db_session.add(PlaylistEntry(playlist_id=playlist.id, track_id=track_a.id, position=0, occurrence_index=0))
    await db_session.commit()

    backend = FakeJellyfinBackend()
    backend.add_item(name="Song A", path="/music-videos/a.mp4")  # identical to Groovarr's own local_path

    async with backend.build_client() as http:
        await sync_media_servers_for_asset(db_session, http, asset_a)

    await db_session.refresh(asset_a)
    assert asset_a.jellyfin_sync_status == SyncStatus.SYNCED


@pytest.mark.asyncio
async def test_jellyfin_path_mismatch_without_remap_stays_pending_sync(db_session):
    """Negative control proving the remap is what does the work below: if
    Jellyfin's container sees the file at a different path and no
    `jellyfin_media_path` is configured, the item is correctly NOT found.
    """
    await _configure_jellyfin(db_session)  # media_path=None
    playlist = await _make_playlist(db_session, jellyfin_enabled=True)
    track_a = await _make_track(db_session, spotify_track_id="a", title="Song A")
    asset_a = await _make_available_asset(db_session, track=track_a, local_path="/music-videos/a.mp4")
    await _link(db_session, playlist=playlist, track=track_a, asset=asset_a)
    db_session.add(PlaylistEntry(playlist_id=playlist.id, track_id=track_a.id, position=0, occurrence_index=0))
    await db_session.commit()

    backend = FakeJellyfinBackend()
    backend.add_item(name="Song A", path="/data/library/a.mp4")  # Jellyfin sees a different mount path

    async with backend.build_client() as http:
        await sync_media_servers_for_asset(db_session, http, asset_a)

    await db_session.refresh(asset_a)
    assert asset_a.jellyfin_sync_status == SyncStatus.PENDING_SYNC


@pytest.mark.asyncio
async def test_jellyfin_media_path_remap_translates_path_before_lookup(db_session):
    """The actual parity fix: with `jellyfin_media_path` configured, Groovarr
    translates its own `/music-videos/...` path into Jellyfin's view of the
    same file before searching — mirroring `plex_media_path`'s existing
    behavior — so the item resolves correctly even though the two containers
    mount the media volume at different paths.
    """
    await _configure_jellyfin(db_session, media_path="/data/library")
    playlist = await _make_playlist(db_session, jellyfin_enabled=True)
    track_a = await _make_track(db_session, spotify_track_id="a", title="Song A")
    asset_a = await _make_available_asset(db_session, track=track_a, local_path="/music-videos/a.mp4")
    await _link(db_session, playlist=playlist, track=track_a, asset=asset_a)
    db_session.add(PlaylistEntry(playlist_id=playlist.id, track_id=track_a.id, position=0, occurrence_index=0))
    await db_session.commit()

    backend = FakeJellyfinBackend()
    backend.add_item(name="Song A", path="/data/library/a.mp4")  # Jellyfin's own view of the same file

    async with backend.build_client() as http:
        await sync_media_servers_for_asset(db_session, http, asset_a)

    await db_session.refresh(asset_a)
    assert asset_a.jellyfin_sync_status == SyncStatus.SYNCED
