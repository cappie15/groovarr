"""Playlist connect / list / sync-now / disconnect endpoints (§10/§60)."""

from datetime import datetime

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.deps import get_http_client
from app.api.pagination import Pagination, paginate, pagination_params
from app.db.models.external_playlist import ExternalPlatform, ExternalPlaylist, ExternalPlaylistSyncState
from app.db.models.spotify import SpotifyPlaylist
from app.db.session import get_session
from app.integrations.spotify.errors import (
    SpotifyAccessDeniedError,
    SpotifyAuthError,
    SpotifyNotFoundError,
    SpotifyRateLimitedError,
)
from app.services.external_playlists import resolve_playlist_collision, sync_external_playlist
from app.services.spotify_sync import connect_playlist, disconnect_playlist, sync_playlist

router = APIRouter(prefix="/api/playlists", tags=["playlists"])


class ConnectPlaylistRequest(BaseModel):
    url_or_id: str


class PlaylistOut(BaseModel):
    spotify_id: str
    name: str
    connected: bool
    finalized: bool
    sync_interval_hours: int
    last_synced_at: datetime | None
    next_sync_at: datetime | None
    track_count: int
    jellyfin_enabled: bool
    jellyfin_name_override: str | None
    plex_enabled: bool
    plex_name_override: str | None


def _to_out(playlist: SpotifyPlaylist) -> PlaylistOut:
    return PlaylistOut(
        spotify_id=playlist.spotify_id,
        name=playlist.name,
        connected=playlist.connected,
        finalized=playlist.finalized_at is not None,
        sync_interval_hours=playlist.sync_interval_hours,
        last_synced_at=playlist.last_synced_at,
        next_sync_at=playlist.next_sync_at,
        track_count=len(playlist.entries),
        jellyfin_enabled=playlist.jellyfin_enabled,
        jellyfin_name_override=playlist.jellyfin_name_override,
        plex_enabled=playlist.plex_enabled,
        plex_name_override=playlist.plex_name_override,
    )


async def _get_by_spotify_id(session: AsyncSession, spotify_id: str) -> SpotifyPlaylist:
    playlist = await session.scalar(
        select(SpotifyPlaylist)
        .options(selectinload(SpotifyPlaylist.entries))
        .where(SpotifyPlaylist.spotify_id == spotify_id)
    )
    if playlist is None:
        raise HTTPException(status_code=404, detail=f"No connected playlist with Spotify ID {spotify_id!r}")
    return playlist


@router.get("", response_model=list[PlaylistOut])
async def list_playlists(
    page: Pagination = Depends(pagination_params), session: AsyncSession = Depends(get_session)
) -> list[PlaylistOut]:
    query = select(SpotifyPlaylist).options(selectinload(SpotifyPlaylist.entries)).order_by(SpotifyPlaylist.id)
    result = await session.execute(paginate(query, page))
    return [_to_out(p) for p in result.scalars().all()]


@router.post("", response_model=PlaylistOut, status_code=201)
async def connect(
    body: ConnectPlaylistRequest,
    session: AsyncSession = Depends(get_session),
    http: httpx.AsyncClient = Depends(get_http_client),
) -> PlaylistOut:
    try:
        playlist = await connect_playlist(session, http, body.url_or_id)
    except ValueError as exc:
        # Covers both a malformed URL/ID (utils.parse_playlist_id) and
        # PlaylistAlreadyFinalizedError, a ValueError subclass.
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except SpotifyNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except SpotifyAccessDeniedError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except SpotifyAuthError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except SpotifyRateLimitedError as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc

    await session.refresh(playlist, attribute_names=["entries"])
    return _to_out(playlist)


@router.post("/{spotify_id}/sync", response_model=PlaylistOut)
async def sync_now(
    spotify_id: str,
    session: AsyncSession = Depends(get_session),
    http: httpx.AsyncClient = Depends(get_http_client),
) -> PlaylistOut:
    playlist = await _get_by_spotify_id(session, spotify_id)
    if playlist.finalized_at is not None:
        raise HTTPException(status_code=409, detail="This playlist is disconnected/finalized and cannot be synced")

    try:
        await sync_playlist(session, http, playlist, force=True)
    except SpotifyNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except SpotifyAccessDeniedError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except SpotifyAuthError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except SpotifyRateLimitedError as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc

    await session.refresh(playlist, attribute_names=["entries"])
    return _to_out(playlist)


@router.post("/{spotify_id}/disconnect", response_model=PlaylistOut)
async def disconnect(spotify_id: str, session: AsyncSession = Depends(get_session)) -> PlaylistOut:
    playlist = await _get_by_spotify_id(session, spotify_id)
    await disconnect_playlist(session, playlist)
    await session.refresh(playlist, attribute_names=["entries"])
    return _to_out(playlist)


class ExternalPlaylistConfigRequest(BaseModel):
    jellyfin_enabled: bool | None = None
    jellyfin_name_override: str | None = None
    plex_enabled: bool | None = None
    plex_name_override: str | None = None


@router.put("/{spotify_id}/external-config", response_model=PlaylistOut)
async def update_external_config(
    spotify_id: str, body: ExternalPlaylistConfigRequest, session: AsyncSession = Depends(get_session)
) -> PlaylistOut:
    """Per-playlist toggles for §55's "Generate Plex Playlist"/"Generate
    Jellyfin Playlist" switches, plus §58's per-platform destination-name
    overrides. Server-level enable/credentials live in Settings
    (app/api/settings.py) — a playlist can only actually sync once both the
    server-level and this per-playlist switch are on.
    """
    playlist = await _get_by_spotify_id(session, spotify_id)
    if body.jellyfin_enabled is not None:
        playlist.jellyfin_enabled = body.jellyfin_enabled
    if body.jellyfin_name_override is not None:
        playlist.jellyfin_name_override = body.jellyfin_name_override or None
    if body.plex_enabled is not None:
        playlist.plex_enabled = body.plex_enabled
    if body.plex_name_override is not None:
        playlist.plex_name_override = body.plex_name_override or None
    await session.commit()
    await session.refresh(playlist, attribute_names=["entries"])
    return _to_out(playlist)


class ExternalPlaylistOut(BaseModel):
    platform: ExternalPlatform
    external_id: str | None
    sync_state: ExternalPlaylistSyncState
    sync_error: str | None
    duplicates_collapsed_count: int | None

    @classmethod
    def from_model(cls, row: ExternalPlaylist) -> "ExternalPlaylistOut":
        return cls(
            platform=row.platform,
            external_id=row.external_id,
            sync_state=row.sync_state,
            sync_error=row.sync_error,
            duplicates_collapsed_count=row.duplicates_collapsed_count,
        )


@router.get("/{spotify_id}/external", response_model=list[ExternalPlaylistOut])
async def get_external_status(
    spotify_id: str, session: AsyncSession = Depends(get_session)
) -> list[ExternalPlaylistOut]:
    playlist = await _get_by_spotify_id(session, spotify_id)
    result = await session.execute(select(ExternalPlaylist).where(ExternalPlaylist.spotify_playlist_id == playlist.id))
    return [ExternalPlaylistOut.from_model(r) for r in result.scalars().all()]


@router.post("/{spotify_id}/sync-external", response_model=list[ExternalPlaylistOut])
async def sync_external(
    spotify_id: str,
    session: AsyncSession = Depends(get_session),
    http: httpx.AsyncClient = Depends(get_http_client),
) -> list[ExternalPlaylistOut]:
    """Manually (re)build this playlist's Jellyfin/Plex playlist right now,
    without waiting for the next acquisition/Spotify-resync trigger."""
    playlist = await _get_by_spotify_id(session, spotify_id)
    outputs: list[ExternalPlaylistOut] = []
    if playlist.jellyfin_enabled:
        await sync_external_playlist(session, http, ExternalPlatform.JELLYFIN, playlist)
    if playlist.plex_enabled:
        await sync_external_playlist(session, http, ExternalPlatform.PLEX, playlist)
    result = await session.execute(select(ExternalPlaylist).where(ExternalPlaylist.spotify_playlist_id == playlist.id))
    outputs = [ExternalPlaylistOut.from_model(r) for r in result.scalars().all()]
    return outputs


class ResolveCollisionRequest(BaseModel):
    action: str  # "adopt" | "cancel"


@router.post("/{spotify_id}/external/{platform}/resolve", response_model=ExternalPlaylistOut)
async def resolve_collision(
    spotify_id: str,
    platform: ExternalPlatform,
    body: ResolveCollisionRequest,
    session: AsyncSession = Depends(get_session),
) -> ExternalPlaylistOut:
    """Resolve a NEEDS_RESOLUTION collision (§59): a same-named,
    Groovarr-unmanaged playlist already exists on the target server.
    "adopt" takes ownership of it (its contents are rebuilt to match Spotify
    on the next sync); "cancel" forgets the conflict so a different name
    (set via /external-config) can be tried on the next sync.
    """
    playlist = await _get_by_spotify_id(session, spotify_id)
    row = await session.scalar(
        select(ExternalPlaylist).where(
            ExternalPlaylist.spotify_playlist_id == playlist.id, ExternalPlaylist.platform == platform
        )
    )
    if row is None:
        raise HTTPException(status_code=404, detail="No external playlist record for this platform")
    try:
        row = await resolve_playlist_collision(session, row, action=body.action)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return ExternalPlaylistOut.from_model(row)
