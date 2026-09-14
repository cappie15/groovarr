"""Settings endpoints. Phase 2 only exposes the Spotify-related fields this
phase needs (§84); later phases extend this router rather than replacing it.
"""

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.settings import MONITOR_BETTER_VERSIONS_WARNING, AppSettings
from app.db.session import get_session
from app.services.settings_service import (
    get_app_settings,
    set_automatic_match_threshold,
    set_default_sync_interval_hours,
    set_download_limits,
    set_jellyfin_config,
    set_lyrics_enabled,
    set_monitor_better_versions_enabled,
    set_plex_config,
    set_spotify_client_credentials,
    set_spotify_user_oauth_enabled,
)

router = APIRouter(prefix="/api/settings", tags=["settings"])


class SettingsOut(BaseModel):
    default_sync_interval_hours: int
    # Never echo the secret back — only whether one is configured.
    spotify_client_id: str | None
    spotify_client_configured: bool
    spotify_user_oauth_enabled: bool
    spotify_needs_reauth: bool

    jellyfin_enabled: bool
    jellyfin_url: str | None
    jellyfin_configured: bool
    jellyfin_user_id: str | None
    jellyfin_library_id: str | None
    jellyfin_media_path: str | None

    plex_enabled: bool
    plex_url: str | None
    plex_configured: bool
    plex_library_section_id: str | None
    plex_media_path: str | None

    monitor_better_versions_enabled: bool
    # Always present, regardless of the toggle's current value, so a UI has
    # no excuse to omit or soften it (§36's explicit requirement).
    monitor_better_versions_warning: str

    # Editable here now (§84 Matching sub-section) — exposed so the
    # Automatic/Manual Search pages can show a candidate's score against the
    # real threshold instead of a guessed one.
    automatic_match_threshold: int

    # §84 Download sub-section (§49/§50) — actually consumed live by
    # app/jobs/download_queue.py and app/services/acquisition.py, not just
    # stored.
    max_concurrent_downloads: int
    max_download_attempts: int

    # §84 Lyrics sub-section (§48) — a pure operator opt-out of the LRCLIB
    # lookup; a miss/failure never blocks acquisition either way regardless
    # of this toggle.
    lyrics_enabled: bool


def _to_out(row: AppSettings) -> SettingsOut:
    return SettingsOut(
        default_sync_interval_hours=row.default_sync_interval_hours,
        spotify_client_id=row.spotify_client_id,
        spotify_client_configured=bool(row.spotify_client_id and row.spotify_client_secret_encrypted),
        spotify_user_oauth_enabled=row.spotify_user_oauth_enabled,
        spotify_needs_reauth=row.spotify_needs_reauth,
        jellyfin_enabled=row.jellyfin_enabled,
        jellyfin_url=row.jellyfin_url,
        jellyfin_configured=bool(row.jellyfin_url and row.jellyfin_api_key_encrypted),
        jellyfin_user_id=row.jellyfin_user_id,
        jellyfin_library_id=row.jellyfin_library_id,
        jellyfin_media_path=row.jellyfin_media_path,
        plex_enabled=row.plex_enabled,
        plex_url=row.plex_url,
        plex_configured=bool(row.plex_url and row.plex_token_encrypted),
        plex_library_section_id=row.plex_library_section_id,
        plex_media_path=row.plex_media_path,
        monitor_better_versions_enabled=row.monitor_better_versions_enabled,
        monitor_better_versions_warning=MONITOR_BETTER_VERSIONS_WARNING,
        automatic_match_threshold=row.automatic_match_threshold,
        max_concurrent_downloads=row.max_concurrent_downloads,
        max_download_attempts=row.max_download_attempts,
        lyrics_enabled=row.lyrics_enabled,
    )


@router.get("", response_model=SettingsOut)
async def read_settings(session: AsyncSession = Depends(get_session)) -> SettingsOut:
    return _to_out(await get_app_settings(session))


class UpdateSyncIntervalRequest(BaseModel):
    default_sync_interval_hours: int = Field(ge=1, le=24 * 30)


@router.put("/sync-interval", response_model=SettingsOut)
async def update_sync_interval(
    body: UpdateSyncIntervalRequest, session: AsyncSession = Depends(get_session)
) -> SettingsOut:
    return _to_out(await set_default_sync_interval_hours(session, body.default_sync_interval_hours))


class SpotifyCredentialsRequest(BaseModel):
    client_id: str
    client_secret: str


@router.put("/spotify-credentials", response_model=SettingsOut)
async def update_spotify_credentials(
    body: SpotifyCredentialsRequest, session: AsyncSession = Depends(get_session)
) -> SettingsOut:
    row = await set_spotify_client_credentials(session, client_id=body.client_id, client_secret=body.client_secret)
    return _to_out(row)


class SpotifyUserOAuthToggleRequest(BaseModel):
    enabled: bool


@router.put("/spotify-user-oauth", response_model=SettingsOut)
async def update_spotify_user_oauth(
    body: SpotifyUserOAuthToggleRequest, session: AsyncSession = Depends(get_session)
) -> SettingsOut:
    return _to_out(await set_spotify_user_oauth_enabled(session, body.enabled))


class JellyfinConfigRequest(BaseModel):
    enabled: bool
    url: str | None = None
    api_key: str | None = None
    user_id: str | None = None
    library_id: str | None = None
    media_path: str | None = None


@router.put("/jellyfin", response_model=SettingsOut)
async def update_jellyfin_config(
    body: JellyfinConfigRequest, session: AsyncSession = Depends(get_session)
) -> SettingsOut:
    row = await set_jellyfin_config(
        session,
        enabled=body.enabled,
        url=body.url,
        api_key=body.api_key,
        user_id=body.user_id,
        library_id=body.library_id,
        media_path=body.media_path,
    )
    return _to_out(row)


class PlexConfigRequest(BaseModel):
    enabled: bool
    url: str | None = None
    token: str | None = None
    library_section_id: str | None = None
    media_path: str | None = None


@router.put("/plex", response_model=SettingsOut)
async def update_plex_config(body: PlexConfigRequest, session: AsyncSession = Depends(get_session)) -> SettingsOut:
    row = await set_plex_config(
        session,
        enabled=body.enabled,
        url=body.url,
        token=body.token,
        library_section_id=body.library_section_id,
        media_path=body.media_path,
    )
    return _to_out(row)


class MonitorBetterVersionsRequest(BaseModel):
    enabled: bool


@router.put("/monitor-better-versions", response_model=SettingsOut)
async def update_monitor_better_versions(
    body: MonitorBetterVersionsRequest, session: AsyncSession = Depends(get_session)
) -> SettingsOut:
    """§36: enabling this may replace an existing music video with a
    *different* video, not merely a higher-resolution copy — see
    `SettingsOut.monitor_better_versions_warning`, always present in every
    response from this router regardless of the toggle's value.
    """
    return _to_out(await set_monitor_better_versions_enabled(session, body.enabled))


class MatchingThresholdRequest(BaseModel):
    automatic_match_threshold: int = Field(ge=0)


@router.put("/matching", response_model=SettingsOut)
async def update_matching_settings(
    body: MatchingThresholdRequest, session: AsyncSession = Depends(get_session)
) -> SettingsOut:
    row = await set_automatic_match_threshold(session, body.automatic_match_threshold)
    return _to_out(row)


class DownloadSettingsRequest(BaseModel):
    max_concurrent_downloads: int = Field(ge=1, le=32)
    max_download_attempts: int = Field(ge=1, le=20)


@router.put("/download", response_model=SettingsOut)
async def update_download_settings(
    body: DownloadSettingsRequest, session: AsyncSession = Depends(get_session)
) -> SettingsOut:
    row = await set_download_limits(
        session,
        max_concurrent_downloads=body.max_concurrent_downloads,
        max_download_attempts=body.max_download_attempts,
    )
    return _to_out(row)


class LyricsSettingsRequest(BaseModel):
    enabled: bool


@router.put("/lyrics", response_model=SettingsOut)
async def update_lyrics_settings(
    body: LyricsSettingsRequest, session: AsyncSession = Depends(get_session)
) -> SettingsOut:
    row = await set_lyrics_enabled(session, body.enabled)
    return _to_out(row)
