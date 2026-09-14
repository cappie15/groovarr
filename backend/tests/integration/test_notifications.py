"""Integration tests for the Connect / notification-connection framework:
CRUD (both at the service layer and through the API route functions, per
test_settings_api.py's convention of calling route functions directly
against `db_session`), event-subscription filtering (a connection only
fires for the events it's subscribed to), and — the critical regression
guarantee the project owner asked for — that `sync_media_servers_for_asset`
only refreshes a provider when an enabled connection is actually subscribed
to the event that just happened, replacing the old unconditional
`AppSettings.jellyfin_enabled`/`plex_enabled` gate without silently changing
behavior for a properly-configured connection.
"""

import httpx
import pytest

from app.api import notifications as notifications_api
from app.api.notifications import NotificationConnectionCreateRequest, NotificationConnectionUpdateRequest
from app.db.models.media import MediaAsset, MediaState, PlaylistMediaReference
from app.db.models.notification import NotificationProvider
from app.db.models.spotify import PlaylistEntry, SpotifyPlaylist, Track
from app.services.external_playlists import sync_media_servers_for_asset
from app.services.notifications import (
    MEDIA_SERVER_REFRESH_EVENTS,
    NOTIFICATION_EVENT_TYPES,
    create_connection,
    delete_connection,
    get_connection,
    get_enabled_connections_for_event,
    list_connections,
    update_connection,
)
from app.services.settings_service import set_jellyfin_config
from tests.fixtures.jellyfin_backend import FakeJellyfinBackend

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# CRUD (service layer)
# ---------------------------------------------------------------------------


async def test_create_connection_persists_name_provider_and_events(db_session) -> None:
    row = await create_connection(
        db_session,
        name="My Jellyfin",
        provider=NotificationProvider.JELLYFIN,
        enabled=True,
        event_types=["acquisition.imported", "replacement.replaced"],
    )
    assert row.id is not None
    assert row.name == "My Jellyfin"
    assert row.provider == NotificationProvider.JELLYFIN
    assert row.enabled is True
    assert {e.event_type for e in row.events} == {"acquisition.imported", "replacement.replaced"}

    all_rows = await list_connections(db_session)
    assert [r.id for r in all_rows] == [row.id]


async def test_create_connection_rejects_blank_name(db_session) -> None:
    with pytest.raises(ValueError, match="name is required"):
        await create_connection(db_session, name="   ", provider=NotificationProvider.PLEX)


async def test_create_connection_rejects_unknown_event_type(db_session) -> None:
    with pytest.raises(ValueError, match="Unknown event type"):
        await create_connection(
            db_session, name="Bad", provider=NotificationProvider.PLEX, event_types=["not.a.real.event"]
        )


async def test_get_connection_missing_raises_lookup_error(db_session) -> None:
    with pytest.raises(LookupError):
        await get_connection(db_session, 999)


async def test_update_connection_replaces_events_and_toggles_enabled(db_session) -> None:
    row = await create_connection(
        db_session, name="Jellyfin", provider=NotificationProvider.JELLYFIN, event_types=["acquisition.imported"]
    )

    updated = await update_connection(
        db_session, row.id, name="Renamed", enabled=False, event_types=["replacement.replaced", "lyrics.found"]
    )
    assert updated.name == "Renamed"
    assert updated.enabled is False
    assert {e.event_type for e in updated.events} == {"replacement.replaced", "lyrics.found"}

    # Partial update (only `enabled`) leaves name/events untouched.
    reenabled = await update_connection(db_session, row.id, enabled=True)
    assert reenabled.enabled is True
    assert reenabled.name == "Renamed"
    assert {e.event_type for e in reenabled.events} == {"replacement.replaced", "lyrics.found"}


async def test_delete_connection_removes_it_and_its_event_rows(db_session) -> None:
    row = await create_connection(
        db_session, name="Plex", provider=NotificationProvider.PLEX, event_types=["acquisition.imported"]
    )
    await delete_connection(db_session, row.id)

    assert await list_connections(db_session) == []
    with pytest.raises(LookupError):
        await get_connection(db_session, row.id)


# ---------------------------------------------------------------------------
# CRUD via the actual API route functions (thin-layer check, per
# test_settings_api.py's convention)
# ---------------------------------------------------------------------------


async def test_api_create_list_update_delete_round_trip(db_session) -> None:
    created = await notifications_api.create_connection(
        NotificationConnectionCreateRequest(
            name="Jellyfin", provider=NotificationProvider.JELLYFIN, enabled=True, event_types=["acquisition.imported"]
        ),
        session=db_session,
    )
    assert created.id is not None
    assert created.event_types == ["acquisition.imported"]

    listed = await notifications_api.list_connections(session=db_session)
    assert [c.id for c in listed] == [created.id]

    updated = await notifications_api.update_connection(
        created.id,
        NotificationConnectionUpdateRequest(enabled=False),
        session=db_session,
    )
    assert updated.enabled is False
    assert updated.event_types == ["acquisition.imported"]  # untouched by a partial update

    await notifications_api.delete_connection(created.id, session=db_session)
    assert await notifications_api.list_connections(session=db_session) == []


async def test_api_list_event_types_matches_service_catalog(db_session) -> None:
    out = await notifications_api.list_event_types()
    assert {e.event_type for e in out} == set(NOTIFICATION_EVENT_TYPES.keys())
    # The two events a default Jellyfin/Plex connection needs are real,
    # listed event types — not a parallel/invented taxonomy.
    for event_type in MEDIA_SERVER_REFRESH_EVENTS:
        assert event_type in NOTIFICATION_EVENT_TYPES


async def test_api_test_connection_surfaces_not_configured_as_422(db_session) -> None:
    row = await create_connection(db_session, name="Jellyfin", provider=NotificationProvider.JELLYFIN)
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(500))) as http:
        with pytest.raises(Exception) as exc_info:
            await notifications_api.test_connection(row.id, session=db_session, http=http)
    # No Jellyfin server configured at all yet — HTTPException(422), not a
    # crash; FastAPI's own HTTPException carries `status_code`.
    assert getattr(exc_info.value, "status_code", None) == 422


# ---------------------------------------------------------------------------
# Event-subscription filtering: a connection only fires for its subscribed
# events.
# ---------------------------------------------------------------------------


async def test_get_enabled_connections_for_event_filters_by_subscription(db_session) -> None:
    subscribed = await create_connection(
        db_session, name="Imports only", provider=NotificationProvider.JELLYFIN, event_types=["acquisition.imported"]
    )
    await create_connection(
        db_session, name="Upgrades only", provider=NotificationProvider.JELLYFIN, event_types=["replacement.replaced"]
    )

    for_import = await get_enabled_connections_for_event(
        db_session, NotificationProvider.JELLYFIN, "acquisition.imported"
    )
    assert [c.id for c in for_import] == [subscribed.id]

    for_unrelated = await get_enabled_connections_for_event(db_session, NotificationProvider.JELLYFIN, "lyrics.found")
    assert for_unrelated == []


async def test_get_enabled_connections_for_event_excludes_disabled(db_session) -> None:
    await create_connection(
        db_session,
        name="Disabled",
        provider=NotificationProvider.JELLYFIN,
        enabled=False,
        event_types=["acquisition.imported"],
    )
    result = await get_enabled_connections_for_event(db_session, NotificationProvider.JELLYFIN, "acquisition.imported")
    assert result == []


async def test_get_enabled_connections_for_event_is_provider_specific(db_session) -> None:
    await create_connection(
        db_session, name="Plex", provider=NotificationProvider.PLEX, event_types=["acquisition.imported"]
    )
    jellyfin_result = await get_enabled_connections_for_event(
        db_session, NotificationProvider.JELLYFIN, "acquisition.imported"
    )
    assert jellyfin_result == []


# ---------------------------------------------------------------------------
# The regression guarantee: sync_media_servers_for_asset only refreshes a
# provider when it has an enabled connection subscribed to the event that
# just happened — this is what replaced the old unconditional
# AppSettings.jellyfin_enabled/plex_enabled gate.
# ---------------------------------------------------------------------------


async def _make_synced_asset(session) -> tuple[SpotifyPlaylist, MediaAsset]:
    playlist = SpotifyPlaylist(spotify_id="p1", name="Playlist", jellyfin_enabled=True)
    session.add(playlist)
    await session.flush()
    track = Track(spotify_track_id="t1", canonical_artist="Artist", canonical_title="Title", duration_ms=200_000)
    session.add(track)
    await session.flush()
    asset = MediaAsset(track_id=track.id, local_path="/music-videos/a.mp4", state=MediaState.AVAILABLE)
    session.add(asset)
    await session.flush()
    session.add(PlaylistEntry(playlist_id=playlist.id, track_id=track.id, position=0, occurrence_index=0))
    session.add(PlaylistMediaReference(playlist_id=playlist.id, track_id=track.id, media_asset_id=asset.id))
    await session.commit()
    return playlist, asset


async def _configure_jellyfin(session) -> None:
    await set_jellyfin_config(
        session, enabled=True, url="http://jellyfin.local", api_key="fake-key", user_id="user-1", library_id=None
    )


async def test_no_connection_at_all_means_no_refresh(db_session) -> None:
    """Without any NotificationConnection row, a Jellyfin/Plex library
    refresh must NOT happen even though the server is fully configured and
    `jellyfin_enabled` is True — the connection is now the actual gate."""
    await _configure_jellyfin(db_session)
    _, asset = await _make_synced_asset(db_session)

    backend = FakeJellyfinBackend()
    async with backend.build_client() as http:
        await sync_media_servers_for_asset(db_session, http, asset, event_type="acquisition.imported")

    assert backend.library_refresh_count == 0


async def test_connection_only_fires_for_its_subscribed_event(db_session) -> None:
    """A connection subscribed only to 'acquisition.imported' must refresh
    on that event but NOT on 'replacement.replaced' — the exact
    event-filtering behavior the Connect framework exists to provide."""
    await _configure_jellyfin(db_session)
    await create_connection(
        db_session, name="Jellyfin", provider=NotificationProvider.JELLYFIN, event_types=["acquisition.imported"]
    )
    _, asset = await _make_synced_asset(db_session)

    backend = FakeJellyfinBackend()
    async with backend.build_client() as http:
        await sync_media_servers_for_asset(db_session, http, asset, event_type="replacement.replaced")
        assert backend.library_refresh_count == 0

        await sync_media_servers_for_asset(db_session, http, asset, event_type="acquisition.imported")
        assert backend.library_refresh_count == 1


async def test_disabling_the_connection_stops_the_refresh(db_session) -> None:
    await _configure_jellyfin(db_session)
    connection = await create_connection(
        db_session, name="Jellyfin", provider=NotificationProvider.JELLYFIN, event_types=["acquisition.imported"]
    )
    _, asset = await _make_synced_asset(db_session)

    backend = FakeJellyfinBackend()
    async with backend.build_client() as http:
        await sync_media_servers_for_asset(db_session, http, asset, event_type="acquisition.imported")
        assert backend.library_refresh_count == 1

        await update_connection(db_session, connection.id, enabled=False)
        await sync_media_servers_for_asset(db_session, http, asset, event_type="acquisition.imported")
        assert backend.library_refresh_count == 1  # unchanged — disabled connection didn't fire
