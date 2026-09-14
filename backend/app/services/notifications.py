"""Sonarr/Radarr-style "Connect" notification framework.

A `NotificationConnection` is a named instance of a provider type (currently
`jellyfin`/`plex` — see `app.db.models.notification` for the schema and the
full architectural rationale) with its own enabled/disabled state and a set
of subscribed event types. This module is:

- the CRUD surface used by `app/api/notifications.py`;
- `NOTIFICATION_EVENT_TYPES`, the single source of truth for which event
  types exist — every key is a real `event_type` string already passed to
  `app.services.history.record_event` at a lifecycle call site (grep for
  `record_event(` across `app/services` if this list needs updating for a
  new one — never invent a parallel taxonomy here);
- `get_enabled_connections_for_event`, the query
  `app.services.external_playlists.sync_media_servers_for_asset` uses to
  decide whether to notify Jellyfin/Plex for a given event on a given asset —
  this REPLACES that function's old unconditional
  `AppSettings.jellyfin_enabled`/`plex_enabled` gate.

For the Jellyfin/Plex providers specifically, "notify" means triggering
their existing library-refresh mechanism
(`app.services.external_playlists`'s per-asset sync) — there is no separate
"send a notification payload" step for these two providers; the refresh IS
the notification. A future webhook/Discord-style provider would instead
build and POST a payload in a new branch wherever this framework's caller
dispatches an event, without touching this module's CRUD or schema.
"""

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.notification import NotificationConnection, NotificationConnectionEvent, NotificationProvider

#: event_type -> short human label, for the Connect page's event-subscription
#: checkboxes. Every key is a real string already used at a
#: `record_event(...)` call site — grouped by the service module that
#: records it, matching that module's own vocabulary exactly (§ task:
#: "reuse it rather than inventing a parallel taxonomy").
NOTIFICATION_EVENT_TYPES: dict[str, str] = {
    # app/services/spotify_sync.py
    "spotify.playlist_synced": "Spotify playlist synced",
    "spotify.playlist_finalized": "Spotify playlist disconnected",
    # app/services/search.py
    "search.missing": "Track has no acceptable candidate (Missing)",
    "search.automatic_selected": "Automatic candidate selected",
    "search.manual_review_required": "Routed to Manual Review",
    "search.manual_selection_made": "Manual candidate selection made",
    # app/services/acquisition.py
    "acquisition.download_started": "Download started",
    "acquisition.download_succeeded": "Download attempt succeeded",
    "acquisition.download_failed": "Download attempt failed",
    "acquisition.imported": "Track imported into the library",
    "acquisition.manual_retry": "Manual retry requested",
    "metadata.tags_written": "Metadata tags written",
    "metadata.tags_failed": "Metadata tag writing failed",
    "metadata.tags_skipped": "Metadata tag writing skipped (non-MP4 container)",
    "lyrics.found": "Lyrics found",
    "lyrics.missing": "Lyrics not found",
    # app/services/replacement.py
    "replacement.replaced": "Track media replaced/upgraded",
    # app/services/deletion.py
    "deletion.removed_unreferenced": "Unreferenced media file deleted",
    # app/services/external_playlists.py
    "external_playlists.synced": "External playlist synced",
    "external_playlists.sync_failed": "External playlist sync failed",
}

#: The exact two events that triggered a Jellyfin/Plex library refresh
#: before this framework existed — see the two
#: `sync_media_servers_for_asset` call sites, in
#: `app.services.acquisition.process_media_asset` (right after
#: "acquisition.imported" is recorded) and
#: `app.services.replacement.replace_track_media` (right after
#: "replacement.replaced" is recorded). Used to seed the migration's default
#: connections so an already-configured server keeps behaving identically;
#: not enforced at dispatch time — a jellyfin/plex connection may be
#: subscribed to any event type, it just has nothing meaningful to refresh
#: for one that isn't asset-related.
MEDIA_SERVER_REFRESH_EVENTS = ("acquisition.imported", "replacement.replaced")

#: Providers a connection may currently target. Kept separate from the
#: `NotificationProvider` enum's own membership check purely so this module
#: has one obvious place to look if that ever needs to differ (it doesn't
#: today) — every member of the enum is presently supported.
SUPPORTED_PROVIDERS = tuple(NotificationProvider)


def _validate_event_types(event_types: list[str]) -> None:
    unknown = sorted(set(event_types) - NOTIFICATION_EVENT_TYPES.keys())
    if unknown:
        raise ValueError(f"Unknown event type(s): {', '.join(unknown)}")


async def list_connections(session: AsyncSession) -> list[NotificationConnection]:
    result = await session.execute(select(NotificationConnection).order_by(NotificationConnection.id))
    return list(result.scalars().unique().all())


async def get_connection(session: AsyncSession, connection_id: int) -> NotificationConnection:
    row = await session.get(NotificationConnection, connection_id)
    if row is None:
        raise LookupError(f"No notification connection with id {connection_id}")
    return row


async def create_connection(
    session: AsyncSession,
    *,
    name: str,
    provider: NotificationProvider,
    enabled: bool = True,
    event_types: list[str] | None = None,
) -> NotificationConnection:
    clean_name = name.strip()
    if not clean_name:
        raise ValueError("Connection name is required")
    event_types = event_types or []
    _validate_event_types(event_types)

    now = datetime.now(UTC)
    row = NotificationConnection(
        name=clean_name,
        provider=provider,
        enabled=enabled,
        config={},
        created_at=now,
        updated_at=now,
        events=[NotificationConnectionEvent(event_type=et) for et in sorted(set(event_types))],
    )
    session.add(row)
    # Every column was set explicitly above (no server-generated defaults to
    # pick up) and the session factory uses expire_on_commit=False, so `row`
    # (including its already-populated `events` collection) stays fully
    # usable after commit with no `session.refresh()` needed.
    await session.commit()
    return row


async def update_connection(
    session: AsyncSession,
    connection_id: int,
    *,
    name: str | None = None,
    enabled: bool | None = None,
    event_types: list[str] | None = None,
) -> NotificationConnection:
    row = await get_connection(session, connection_id)
    if name is not None:
        clean_name = name.strip()
        if not clean_name:
            raise ValueError("Connection name is required")
        row.name = clean_name
    if enabled is not None:
        row.enabled = enabled
    if event_types is not None:
        _validate_event_types(event_types)
        # Replace wholesale rather than diffing — a connection's event set is
        # small (at most len(NOTIFICATION_EVENT_TYPES)) and this can never
        # leave a stale row behind the way a manual add/remove diff could.
        row.events = [NotificationConnectionEvent(event_type=et) for et in sorted(set(event_types))]
    row.updated_at = datetime.now(UTC)
    await session.commit()
    return row


async def delete_connection(session: AsyncSession, connection_id: int) -> None:
    row = await get_connection(session, connection_id)
    await session.delete(row)
    await session.commit()


async def test_connection(session: AsyncSession, http, connection_id: int) -> None:
    """Best-effort connectivity check for one connection's underlying
    server. Raises `ValueError` (server not configured) or the provider's own
    error type on failure — both safe to surface as 4xx/502 API errors,
    matching `app/api/media_servers.py`'s existing `/test` endpoints, which
    this reuses rather than duplicating (a connection's provider IS the
    single already-configured Jellyfin/Plex server, per §97 — there is
    nothing per-connection to test beyond that).
    """
    from app.integrations.jellyfin.client import JellyfinClient
    from app.integrations.plex.client import PlexClient
    from app.services.settings_service import get_jellyfin_connection, get_plex_credentials

    row = await get_connection(session, connection_id)
    if row.provider == NotificationProvider.JELLYFIN:
        conn = await get_jellyfin_connection(session)
        client = JellyfinClient(http, conn.url, conn.api_key)
        await client.test_connection()
    elif row.provider == NotificationProvider.PLEX:
        creds = await get_plex_credentials(session)
        client = PlexClient(http, creds.url, creds.token)
        await client.test_connection()


async def get_enabled_connections_for_event(
    session: AsyncSession, provider: NotificationProvider, event_type: str
) -> list[NotificationConnection]:
    """Enabled connections of `provider` subscribed to `event_type` — the
    exact question `sync_media_servers_for_asset` needs answered to decide
    whether to refresh that provider's library for a given lifecycle event,
    in place of the old blanket `AppSettings.jellyfin_enabled`/`plex_enabled`
    check.
    """
    result = await session.execute(
        select(NotificationConnection)
        .join(NotificationConnectionEvent, NotificationConnectionEvent.connection_id == NotificationConnection.id)
        .where(
            NotificationConnection.provider == provider,
            NotificationConnection.enabled.is_(True),
            NotificationConnectionEvent.event_type == event_type,
        )
    )
    return list(result.scalars().unique().all())


async def provider_has_subscriber(session: AsyncSession, provider: NotificationProvider, event_type: str) -> bool:
    """Cheap boolean form of `get_enabled_connections_for_event` for a
    call site that only needs to know whether to bother at all."""
    return bool(await get_enabled_connections_for_event(session, provider, event_type))
