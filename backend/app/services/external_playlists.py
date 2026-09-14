"""Playlist reconstruction and library-refresh orchestration for Jellyfin/Plex
(Phase 8, §53-61 of the architecture doc).

Two layers, deliberately independent (§61):

- `sync_media_servers_for_asset` runs once a `MediaAsset` becomes AVAILABLE/
  INCOMPLETE (called from app/services/acquisition.py): triggers a library
  refresh, best-effort-confirms the new file is discoverable, and updates
  ONLY `MediaAsset.jellyfin_sync_status`/`plex_sync_status` — never
  `MediaAsset.state`, so a server outage can never roll back a successful
  acquisition.
- `sync_external_playlist` rebuilds one playlist's membership/order on one
  server from the current `PlaylistEntry`/`PlaylistMediaReference` rows,
  omitting any track that isn't discoverable yet (§27/§28 — no placeholders).

Content-changing syncs use a delete-then-recreate strategy rather than an
incremental item diff: neither Jellyfin nor Plex documents a fully reliable
single-item-removal primitive for this use case, and recreation trivially
handles adds/removes/reorders/duplicate-preservation correctly every time, at
the cost of a few extra API calls — an acceptable trade given how often this
actually runs (per-acquisition and per-Spotify-resync, not a tight loop).

A discovered file is matched to its external-server item by an *exact* path
match after applying the optional `jellyfin_media_path`/`plex_media_path`
remap (never by title alone, to avoid ever picking the wrong item — precision
over recall, §104). Both platforms have parity here: if either server's
container mounts the media volume at a different path than Groovarr's own,
set the corresponding `*_media_path` setting so path matching still succeeds
(left null on either side, Groovarr assumes that server's view of the path is
identical to its own).
"""

import asyncio
import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpx
import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.db.models.external_playlist import ExternalPlatform, ExternalPlaylist, ExternalPlaylistSyncState
from app.db.models.media import MediaAsset, MediaState, PlaylistMediaReference, SyncStatus
from app.db.models.settings import AppSettings
from app.db.models.spotify import PlaylistEntry, SpotifyPlaylist, Track
from app.integrations.jellyfin.client import JellyfinClient
from app.integrations.jellyfin.errors import JellyfinError, JellyfinScanTimeoutError
from app.integrations.plex.client import PlexClient
from app.integrations.plex.errors import PlexError
from app.services.history import record_event
from app.services.settings_service import get_app_settings, get_jellyfin_credentials, get_plex_credentials

logger = structlog.get_logger(__name__)

_LIVE_MEDIA_STATES = (MediaState.AVAILABLE, MediaState.INCOMPLETE)

# Bounded poll budget for a Jellyfin scan to return to Idle (§56 — "do not
# hammer APIs"; 10.11.x/large-flat-folder scans were researched as
# potentially slow — see JellyfinScanTimeoutError).
_JELLYFIN_SCAN_POLL_ATTEMPTS = 6
_JELLYFIN_SCAN_POLL_INTERVAL_S = 5.0


@dataclass
class PlaylistSyncOutcome:
    state: ExternalPlaylistSyncState
    item_count: int
    duplicates_collapsed: int | None = None


# --- Per-asset: library refresh + discoverability + triggering playlist sync ---


async def sync_media_servers_for_asset(session: AsyncSession, http: httpx.AsyncClient, asset: MediaAsset) -> None:
    """Best-effort: any failure here is caught, logged, and reflected only in
    `MediaAsset.jellyfin_sync_status`/`plex_sync_status` — never propagated,
    and `MediaAsset.state` is never touched (§61).
    """
    app_settings = await get_app_settings(session)

    refs = await session.execute(
        select(PlaylistMediaReference.playlist_id).where(PlaylistMediaReference.media_asset_id == asset.id)
    )
    playlist_ids = list(refs.scalars().all())
    if not playlist_ids:
        return
    playlists_result = await session.execute(select(SpotifyPlaylist).where(SpotifyPlaylist.id.in_(playlist_ids)))
    playlists = list(playlists_result.scalars().all())

    if app_settings.jellyfin_enabled and any(p.jellyfin_enabled for p in playlists):
        await _sync_jellyfin_for_asset(session, http, asset, [p for p in playlists if p.jellyfin_enabled])

    if app_settings.plex_enabled and any(p.plex_enabled for p in playlists):
        await _sync_plex_for_asset(session, http, asset, [p for p in playlists if p.plex_enabled])


async def _sync_jellyfin_for_asset(
    session: AsyncSession, http: httpx.AsyncClient, asset: MediaAsset, playlists: list[SpotifyPlaylist]
) -> None:
    try:
        creds = await get_jellyfin_credentials(session)
        app_settings = await get_app_settings(session)
        client = JellyfinClient(http, creds.url, creds.api_key)
        await client.trigger_library_refresh()
        try:
            await _poll_jellyfin_idle(client)
        except JellyfinScanTimeoutError:
            asset.jellyfin_sync_status = SyncStatus.PENDING_SYNC
            await session.commit()
            return

        track = await session.get(Track, asset.track_id) if asset.track_id else None
        item = None
        if track is not None and asset.local_path:
            expected_path = _map_to_jellyfin_path(asset.local_path, get_settings(), app_settings)
            item = await client.find_item_by_path(
                creds.user_id, search_term=track.canonical_title, expected_path=expected_path
            )
        asset.jellyfin_sync_status = SyncStatus.SYNCED if item is not None else SyncStatus.PENDING_SYNC
        await session.commit()

        for playlist in playlists:
            await sync_external_playlist(session, http, ExternalPlatform.JELLYFIN, playlist)

    except JellyfinError as exc:
        logger.warning("external_playlists.jellyfin_asset_sync_failed", media_asset_id=asset.id, error=str(exc))
        asset.jellyfin_sync_status = SyncStatus.FAILED_SYNC
        await session.commit()
    except ValueError as exc:  # Jellyfin not fully configured yet
        logger.info("external_playlists.jellyfin_not_configured", error=str(exc))


async def _poll_jellyfin_idle(client: JellyfinClient) -> None:
    for _ in range(_JELLYFIN_SCAN_POLL_ATTEMPTS):
        state = await client.get_library_scan_task_state()
        if state is None or state == "Idle":
            return
        await asyncio.sleep(_JELLYFIN_SCAN_POLL_INTERVAL_S)
    raise JellyfinScanTimeoutError("Jellyfin library scan did not return to Idle within the poll budget")


async def _sync_plex_for_asset(
    session: AsyncSession, http: httpx.AsyncClient, asset: MediaAsset, playlists: list[SpotifyPlaylist]
) -> None:
    try:
        creds = await get_plex_credentials(session)
        app_settings = await get_app_settings(session)
        if not app_settings.plex_library_section_id:
            raise ValueError("No Plex library section configured")
        client = PlexClient(http, creds.url, creds.token)

        settings = get_settings()
        scan_path = app_settings.plex_media_path or str(settings.media_dir)
        await client.refresh_section(app_settings.plex_library_section_id, path=scan_path)

        track = await session.get(Track, asset.track_id) if asset.track_id else None
        found = False
        if track is not None:
            expected_path = _map_to_plex_path(asset.local_path, settings, app_settings)
            results = await client.search_section(app_settings.plex_library_section_id, title=track.canonical_title)
            found = any(r.file_path == expected_path for r in results)
        asset.plex_sync_status = SyncStatus.SYNCED if found else SyncStatus.PENDING_SYNC
        await session.commit()

        for playlist in playlists:
            await sync_external_playlist(session, http, ExternalPlatform.PLEX, playlist)

    except PlexError as exc:
        logger.warning("external_playlists.plex_asset_sync_failed", media_asset_id=asset.id, error=str(exc))
        asset.plex_sync_status = SyncStatus.FAILED_SYNC
        await session.commit()
    except ValueError as exc:  # Plex not fully configured yet
        logger.info("external_playlists.plex_not_configured", error=str(exc))


def _map_to_plex_path(local_path: str | None, settings: Any, app_settings: AppSettings) -> str | None:
    """Rewrite Groovarr's own view of a file's path to how Plex sees the same
    file, if `plex_media_path` overrides the media root (§9/§53 — the two
    containers may mount the same volume at different paths).
    """
    if not local_path:
        return None
    if not app_settings.plex_media_path:
        return local_path
    media_dir = str(settings.media_dir)
    if local_path.startswith(media_dir):
        return app_settings.plex_media_path.rstrip("/") + local_path[len(media_dir) :]
    return local_path


def _map_to_jellyfin_path(local_path: str | None, settings: Any, app_settings: AppSettings) -> str | None:
    """Rewrite Groovarr's own view of a file's path to how Jellyfin sees the
    same file, if `jellyfin_media_path` overrides the media root — the exact
    same remap pattern as `_map_to_plex_path` above, applied on the Jellyfin
    side for parity (this was originally a known gap, see the module
    docstring's history note).
    """
    if not local_path:
        return None
    if not app_settings.jellyfin_media_path:
        return local_path
    media_dir = str(settings.media_dir)
    if local_path.startswith(media_dir):
        return app_settings.jellyfin_media_path.rstrip("/") + local_path[len(media_dir) :]
    return local_path


# --- Per-playlist: rebuild membership/order on one platform --------------------


async def sync_external_playlist(
    session: AsyncSession, http: httpx.AsyncClient, platform: ExternalPlatform, playlist: SpotifyPlaylist
) -> PlaylistSyncOutcome:
    """Rebuild `playlist`'s membership/order on `platform` from its current
    `PlaylistEntry` rows. Skips any entry whose track has no live,
    discoverable `MediaAsset` — a Missing/not-yet-indexed track is simply
    omitted, never a placeholder (§27/§28).
    """
    row = await session.scalar(
        select(ExternalPlaylist).where(
            ExternalPlaylist.platform == platform, ExternalPlaylist.spotify_playlist_id == playlist.id
        )
    )
    if row is None:
        row = ExternalPlaylist(platform=platform, spotify_playlist_id=playlist.id)
        session.add(row)
        await session.flush()

    if row.sync_state == ExternalPlaylistSyncState.NEEDS_RESOLUTION and not row.adopted:
        # Waiting on an explicit operator decision (§59) — touch nothing.
        return PlaylistSyncOutcome(state=row.sync_state, item_count=0)

    desired_name = (
        playlist.jellyfin_name_override if platform == ExternalPlatform.JELLYFIN else playlist.plex_name_override
    ) or playlist.name

    try:
        client, ordered_ids = await _resolve_ordered_items(session, http, platform, playlist)
        snapshot = hashlib.sha256(",".join(ordered_ids).encode()).hexdigest()

        unchanged = (
            row.external_id is not None
            and row.last_synced_snapshot == snapshot
            and row.last_synced_name == desired_name
        )
        if unchanged:
            row.sync_state = ExternalPlaylistSyncState.SYNCED
            await session.commit()
            return PlaylistSyncOutcome(state=row.sync_state, item_count=len(ordered_ids))

        if row.external_id is None:
            # First-ever sync for this playlist on this platform: check for a
            # pre-existing, Groovarr-unmanaged playlist under the same name
            # before creating anything (§59).
            existing = await _list_external_playlist_names(session, http, client, platform)
            collision = next((eid for eid, name in existing if name == desired_name), None)
            if collision is not None:
                row.sync_state = ExternalPlaylistSyncState.NEEDS_RESOLUTION
                row.external_id = collision
                row.sync_error = (
                    f'A playlist named "{desired_name}" already exists on {platform.value} '
                    "and is not managed by Groovarr."
                )
                await session.commit()
                return PlaylistSyncOutcome(state=row.sync_state, item_count=0)

            if not ordered_ids:
                row.sync_state = ExternalPlaylistSyncState.PENDING
                await session.commit()
                return PlaylistSyncOutcome(state=row.sync_state, item_count=0)

            row.external_id = await _create_playlist(session, http, client, platform, desired_name, ordered_ids)
        else:
            if not ordered_ids:
                # Everything that was available has since become
                # unavailable again — leave the last-known-good playlist in
                # place rather than deleting it over a transient gap.
                row.sync_state = ExternalPlaylistSyncState.SYNCED
                await session.commit()
                return PlaylistSyncOutcome(state=row.sync_state, item_count=0)
            if row.last_synced_name != desired_name:
                await _rename_playlist(session, client, platform, row.external_id, desired_name)
            await client.delete_playlist(row.external_id)
            row.external_id = await _create_playlist(session, http, client, platform, desired_name, ordered_ids)

        duplicates_collapsed = None
        if platform == ExternalPlatform.PLEX:
            actual_count = await client.get_playlist_item_count(row.external_id)
            duplicates_collapsed = max(len(ordered_ids) - actual_count, 0)

        row.last_synced_name = desired_name
        row.last_synced_snapshot = snapshot
        row.sync_state = ExternalPlaylistSyncState.SYNCED
        row.sync_error = None
        row.duplicates_collapsed_count = duplicates_collapsed
        row.last_synced_at = datetime.now(UTC)
        record_event(
            session,
            event_type="external_playlists.synced",
            detail=(
                f"{platform.value} playlist {desired_name!r} synced: {len(ordered_ids)} items"
                + (f", {duplicates_collapsed} duplicate(s) collapsed" if duplicates_collapsed else "")
                + "."
            ),
        )
        await session.commit()

        logger.info(
            "external_playlists.synced",
            platform=platform.value,
            spotify_playlist_id=playlist.id,
            items=len(ordered_ids),
            duplicates_collapsed=duplicates_collapsed,
        )
        return PlaylistSyncOutcome(
            state=row.sync_state, item_count=len(ordered_ids), duplicates_collapsed=duplicates_collapsed
        )

    except (JellyfinError, PlexError, ValueError) as exc:
        row.sync_state = ExternalPlaylistSyncState.FAILED
        row.sync_error = str(exc)
        record_event(
            session,
            event_type="external_playlists.sync_failed",
            detail=f"{platform.value} playlist sync failed: {exc}",
        )
        await session.commit()
        logger.warning(
            "external_playlists.sync_failed", platform=platform.value, spotify_playlist_id=playlist.id, error=str(exc)
        )
        return PlaylistSyncOutcome(state=row.sync_state, item_count=0)


async def resolve_playlist_collision(session: AsyncSession, row: ExternalPlaylist, *, action: str) -> ExternalPlaylist:
    """Explicitly resolve a NEEDS_RESOLUTION collision (§59): "adopt" takes
    ownership of the pre-existing external playlist found under this name
    (its content will be rebuilt to match Spotify order on the next sync,
    exactly like any other Groovarr-owned playlist); "cancel" gives up on
    this platform for this Spotify playlist entirely, forgetting the
    conflicting id. Choosing a different destination name instead of either
    is done by setting a name override via the playlist endpoints and then
    calling "cancel" here to clear the stale collision record so the next
    sync starts fresh under the new name.
    """
    if row.sync_state != ExternalPlaylistSyncState.NEEDS_RESOLUTION:
        raise ValueError("This external playlist is not awaiting a collision resolution")

    if action == "adopt":
        row.adopted = True
        row.sync_state = ExternalPlaylistSyncState.PENDING
    elif action == "cancel":
        row.external_id = None
        row.adopted = False
        row.sync_error = None
        row.sync_state = ExternalPlaylistSyncState.PENDING
    else:
        raise ValueError(f"Unknown resolution action {action!r} — expected 'adopt' or 'cancel'")

    await session.commit()
    await session.refresh(row)
    return row


# --- internals ------------------------------------------------------------------


async def _resolve_ordered_items(
    session: AsyncSession, http: httpx.AsyncClient, platform: ExternalPlatform, playlist: SpotifyPlaylist
) -> tuple[Any, list[str]]:
    jellyfin_user_id: str | None = None
    app_settings = await get_app_settings(session)

    client: Any
    if platform == ExternalPlatform.JELLYFIN:
        creds = await get_jellyfin_credentials(session)
        client = JellyfinClient(http, creds.url, creds.api_key)
        jellyfin_user_id = creds.user_id
    else:
        creds = await get_plex_credentials(session)
        if not app_settings.plex_library_section_id:
            raise ValueError("No Plex library section configured")
        client = PlexClient(http, creds.url, creds.token)

    settings = get_settings()

    entries_result = await session.execute(
        select(PlaylistEntry).where(PlaylistEntry.playlist_id == playlist.id).order_by(PlaylistEntry.position)
    )
    entries = list(entries_result.scalars().all())

    external_id_cache: dict[int, str | None] = {}
    ordered_ids: list[str] = []

    for entry in entries:
        ref = await session.scalar(
            select(PlaylistMediaReference).where(
                PlaylistMediaReference.playlist_id == playlist.id, PlaylistMediaReference.track_id == entry.track_id
            )
        )
        if ref is None:
            continue
        asset = await session.get(MediaAsset, ref.media_asset_id)
        if asset is None or asset.state not in _LIVE_MEDIA_STATES or not asset.local_path:
            continue

        if asset.id not in external_id_cache:
            track = await session.get(Track, asset.track_id) if asset.track_id else None
            if track is None:
                external_id_cache[asset.id] = None
            elif platform == ExternalPlatform.JELLYFIN:
                item = await client.find_item_by_path(
                    jellyfin_user_id, search_term=track.canonical_title, expected_path=asset.local_path
                )
                external_id_cache[asset.id] = item.id if item else None
            else:
                expected_path = _map_to_plex_path(asset.local_path, settings, app_settings)
                results = await client.search_section(app_settings.plex_library_section_id, title=track.canonical_title)
                match = next((r for r in results if r.file_path == expected_path), None)
                external_id_cache[asset.id] = match.rating_key if match else None

        ext_id = external_id_cache[asset.id]
        if ext_id is not None:
            ordered_ids.append(ext_id)

    return client, ordered_ids


async def _list_external_playlist_names(
    session: AsyncSession, http: httpx.AsyncClient, client: Any, platform: ExternalPlatform
) -> list[tuple[str, str]]:
    if platform == ExternalPlatform.JELLYFIN:
        creds = await get_jellyfin_credentials(session)
        playlists = await client.list_playlists(creds.user_id)
        return [(p.id, p.name) for p in playlists]
    playlists = await client.list_playlists()
    return [(p.rating_key, p.title) for p in playlists]


async def _create_playlist(
    session: AsyncSession,
    http: httpx.AsyncClient,
    client: Any,
    platform: ExternalPlatform,
    name: str,
    item_ids: list[str],
) -> str:
    if platform == ExternalPlatform.JELLYFIN:
        creds = await get_jellyfin_credentials(session)
        return await client.create_playlist(user_id=creds.user_id, name=name, item_ids=item_ids)  # type: ignore[no-any-return]
    return await client.create_playlist(title=name, rating_keys=item_ids)  # type: ignore[no-any-return]


async def _rename_playlist(
    session: AsyncSession, client: Any, platform: ExternalPlatform, external_id: str, name: str
) -> None:
    if platform == ExternalPlatform.JELLYFIN:
        creds = await get_jellyfin_credentials(session)
        await client.rename_playlist(external_id, name, user_id=creds.user_id)
    else:
        await client.rename_playlist(external_id, name)
