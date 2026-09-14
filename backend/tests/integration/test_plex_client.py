"""Integration tests for PlexClient against a faked Plex API (§92) — no live
server, no real network access.
"""

import pytest

from app.integrations.plex.client import PlexClient
from tests.fixtures.plex_backend import FakePlexBackend


@pytest.mark.asyncio
async def test_connection_test_and_list_sections():
    backend = FakePlexBackend()
    async with backend.build_client() as http:
        client = PlexClient(http, "http://plex.local", "fake-token")
        await client.test_connection()
        sections = await client.list_library_sections()
    assert sections[0].key == "1"
    assert sections[0].type == "artist"


@pytest.mark.asyncio
async def test_refresh_section_passes_the_folder_path():
    backend = FakePlexBackend()
    async with backend.build_client() as http:
        client = PlexClient(http, "http://plex.local", "fake-token")
        await client.refresh_section("1", path="/music-videos")
    assert backend.refresh_calls == [("1", "/music-videos")]


@pytest.mark.asyncio
async def test_create_playlist_collapses_duplicate_rating_keys():
    """Reproduces Plex's own real server-side dedup (§2 row B) — sending the
    same ratingKey twice must not produce two entries.
    """
    backend = FakePlexBackend()
    async with backend.build_client() as http:
        client = PlexClient(http, "http://plex.local", "fake-token")
        rating_key = await client.create_playlist(title="My Playlist", rating_keys=["10", "20", "10"])
        count = await client.get_playlist_item_count(rating_key)
    assert count == 2  # collapsed from 3 requested to 2 unique


@pytest.mark.asyncio
async def test_search_section_returns_file_path():
    backend = FakePlexBackend()
    backend.add_item("1", title="Song A", file_path="/music-videos/a.mp4")
    async with backend.build_client() as http:
        client = PlexClient(http, "http://plex.local", "fake-token")
        results = await client.search_section("1", title="Song A")
    assert results[0].file_path == "/music-videos/a.mp4"
