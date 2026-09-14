"""Integration tests for JellyfinClient against a faked Jellyfin API (§92) —
no live server, no real network access.
"""

import pytest

from app.integrations.jellyfin.client import JellyfinClient
from app.integrations.jellyfin.errors import (
    JellyfinMissingUserError,
    JellyfinPlaylistsDirectoryMissingError,
    JellyfinScanTimeoutError,
)
from tests.fixtures.jellyfin_backend import FakeJellyfinBackend


@pytest.mark.asyncio
async def test_connection_test_and_list_users():
    backend = FakeJellyfinBackend()
    async with backend.build_client() as http:
        client = JellyfinClient(http, "http://jellyfin.local", "fake-key")
        await client.test_connection()
        users = await client.list_users()
    assert users[0].id == "user-1"
    assert users[0].name == "ben"


@pytest.mark.asyncio
async def test_create_playlist_without_user_id_never_hits_the_wire():
    """The Guid.Empty bug (issue #12999) avoidance: refusing locally means
    zero HTTP calls are made at all for a missing user, not just "the right
    error surfaces after a failed call".
    """
    backend = FakeJellyfinBackend()
    async with backend.build_client() as http:
        client = JellyfinClient(http, "http://jellyfin.local", "fake-key")
        with pytest.raises(JellyfinMissingUserError):
            await client.create_playlist(user_id="", name="Test", item_ids=["item-1"])
    assert backend.create_playlist_calls == []


@pytest.mark.asyncio
async def test_create_playlist_with_real_user_id_succeeds_and_sends_it():
    backend = FakeJellyfinBackend()
    item_id = backend.add_item(name="Song A", path="/music-videos/a.mp4")
    async with backend.build_client() as http:
        client = JellyfinClient(http, "http://jellyfin.local", "fake-key")
        playlist_id = await client.create_playlist(user_id="user-1", name="My Playlist", item_ids=[item_id])
    assert playlist_id in backend.playlists
    assert backend.create_playlist_calls[0]["UserId"] == "user-1"


@pytest.mark.asyncio
async def test_fresh_install_parent_folder_error_is_surfaced_clearly():
    backend = FakeJellyfinBackend()
    backend.parent_folder_missing = True
    async with backend.build_client() as http:
        client = JellyfinClient(http, "http://jellyfin.local", "fake-key")
        with pytest.raises(JellyfinPlaylistsDirectoryMissingError):
            await client.create_playlist(user_id="user-1", name="First Ever Playlist", item_ids=["x"])


@pytest.mark.asyncio
async def test_find_item_by_path_requires_exact_path_match():
    backend = FakeJellyfinBackend()
    backend.add_item(name="Song A", path="/music-videos/a.mp4")
    async with backend.build_client() as http:
        client = JellyfinClient(http, "http://jellyfin.local", "fake-key")
        found = await client.find_item_by_path("user-1", search_term="Song A", expected_path="/music-videos/a.mp4")
        not_found = await client.find_item_by_path(
            "user-1", search_term="Song A", expected_path="/some/other/path.mp4"
        )
    assert found is not None and found.path == "/music-videos/a.mp4"
    assert not_found is None


@pytest.mark.asyncio
async def test_scan_poll_returns_immediately_when_already_idle():
    from app.services.external_playlists import _poll_jellyfin_idle

    backend = FakeJellyfinBackend()
    async with backend.build_client() as http:
        client = JellyfinClient(http, "http://jellyfin.local", "fake-key")
        await _poll_jellyfin_idle(client)  # must not raise / must not hang


@pytest.mark.asyncio
async def test_scan_poll_succeeds_after_a_few_polls(monkeypatch):
    import app.services.external_playlists as ext

    monkeypatch.setattr(ext, "_JELLYFIN_SCAN_POLL_INTERVAL_S", 0.01)

    backend = FakeJellyfinBackend()
    backend.set_slow_scan(polls_until_idle=3)
    async with backend.build_client() as http:
        client = JellyfinClient(http, "http://jellyfin.local", "fake-key")
        await ext._poll_jellyfin_idle(client)  # must not raise


@pytest.mark.asyncio
async def test_scan_poll_times_out_gracefully_if_never_idle(monkeypatch):
    import app.services.external_playlists as ext

    monkeypatch.setattr(ext, "_JELLYFIN_SCAN_POLL_INTERVAL_S", 0.01)

    backend = FakeJellyfinBackend()
    backend.set_slow_scan(polls_until_idle=1_000_000)  # effectively never
    async with backend.build_client() as http:
        client = JellyfinClient(http, "http://jellyfin.local", "fake-key")
        with pytest.raises(JellyfinScanTimeoutError):
            await ext._poll_jellyfin_idle(client)
