"""Access to the single `AppSettings` row (runtime, UI-editable settings) —
distinct from `app.core.config.Settings`, which is process/deployment
config from environment variables (§84).

Spotify Client ID/Secret can come from either source: the DB row lets an
operator set/change them from the Settings UI, while the env vars are the
zero-config path for a pure Docker Compose deployment. The DB value wins
when present.
"""

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.secrets import decrypt_secret, encrypt_secret
from app.db.models.settings import SETTINGS_SINGLETON_ID, AppSettings


async def get_app_settings(session: AsyncSession) -> AppSettings:
    """Return the singleton settings row, creating it with defaults on first
    use — so callers never have to special-case "row doesn't exist yet".
    """
    row = await session.get(AppSettings, SETTINGS_SINGLETON_ID)
    if row is None:
        row = AppSettings(id=SETTINGS_SINGLETON_ID)
        session.add(row)
        await session.commit()
        await session.refresh(row)
    return row


@dataclass(frozen=True)
class SpotifyCredentials:
    client_id: str
    client_secret: str


async def get_effective_spotify_credentials(session: AsyncSession) -> SpotifyCredentials:
    """Resolve the Spotify Client ID/Secret to actually use: the DB-stored
    values (settable from the UI) if present, otherwise the
    SPOTIFY_CLIENT_ID/SPOTIFY_CLIENT_SECRET environment variables.

    Raises `ValueError` (safe to surface as a 4xx API error) if neither
    source provides both values — the default Client Credentials Flow can't
    do anything without them.
    """
    row = await get_app_settings(session)
    env = get_settings()

    client_id = row.spotify_client_id or env.spotify_client_id
    if row.spotify_client_secret_encrypted:
        client_secret = decrypt_secret(row.spotify_client_secret_encrypted)
    else:
        client_secret = env.spotify_client_secret

    if not client_id or not client_secret:
        raise ValueError(
            "No Spotify Client ID/Secret configured. Set SPOTIFY_CLIENT_ID and "
            "SPOTIFY_CLIENT_SECRET (env vars or Settings) from a free Spotify "
            "Developer Dashboard app — no user login is required for public playlists."
        )
    return SpotifyCredentials(client_id=client_id, client_secret=client_secret)


async def set_spotify_client_credentials(
    session: AsyncSession, *, client_id: str, client_secret: str
) -> AppSettings:
    row = await get_app_settings(session)
    row.spotify_client_id = client_id
    row.spotify_client_secret_encrypted = encrypt_secret(client_secret)
    await session.commit()
    await session.refresh(row)
    return row


async def set_default_sync_interval_hours(session: AsyncSession, hours: int) -> AppSettings:
    if hours < 1:
        raise ValueError("Sync interval must be at least 1 hour")
    row = await get_app_settings(session)
    row.default_sync_interval_hours = hours
    await session.commit()
    await session.refresh(row)
    return row


async def set_spotify_user_oauth_enabled(session: AsyncSession, enabled: bool) -> AppSettings:
    row = await get_app_settings(session)
    row.spotify_user_oauth_enabled = enabled
    if not enabled:
        # Turning the optional feature off drops any stored token — there is
        # nothing meaningful to keep it around for, and it avoids a stale
        # credential lingering silently.
        row.spotify_refresh_token_encrypted = None
        row.spotify_refresh_token_obtained_at = None
        row.spotify_needs_reauth = False
    await session.commit()
    await session.refresh(row)
    return row


async def store_spotify_user_refresh_token(session: AsyncSession, refresh_token: str) -> AppSettings:
    from datetime import UTC, datetime

    row = await get_app_settings(session)
    row.spotify_refresh_token_encrypted = encrypt_secret(refresh_token)
    row.spotify_refresh_token_obtained_at = datetime.now(UTC)
    row.spotify_needs_reauth = False
    await session.commit()
    await session.refresh(row)
    return row


async def mark_spotify_needs_reauth(session: AsyncSession) -> None:
    row = await get_app_settings(session)
    row.spotify_needs_reauth = True
    await session.commit()


async def set_automatic_match_threshold(session: AsyncSession, threshold: int) -> AppSettings:
    """§21/§86: the weighted score at/above which a candidate is selected
    automatically. Not bounded to the scoring scale's exact min/max here —
    `app.matching.scoring` documents the practical range; this just persists
    whatever the operator sets, matching the existing settings setters'
    style of doing only the validation that prevents a nonsensical value."""
    if threshold < 0:
        raise ValueError("automatic_match_threshold cannot be negative")
    row = await get_app_settings(session)
    row.automatic_match_threshold = threshold
    await session.commit()
    await session.refresh(row)
    return row


async def set_download_limits(
    session: AsyncSession, *, max_concurrent_downloads: int, max_download_attempts: int
) -> AppSettings:
    """§49/§50: bounded worker-pool concurrency and the retry ceiling before
    a MediaAsset lands on DOWNLOAD_FAILED."""
    if max_concurrent_downloads < 1:
        raise ValueError("max_concurrent_downloads must be at least 1")
    if max_download_attempts < 1:
        raise ValueError("max_download_attempts must be at least 1")
    row = await get_app_settings(session)
    row.max_concurrent_downloads = max_concurrent_downloads
    row.max_download_attempts = max_download_attempts
    await session.commit()
    await session.refresh(row)
    return row


async def set_lyrics_enabled(session: AsyncSession, enabled: bool) -> AppSettings:
    """§7 Phase 7: purely an operator opt-out of the LRCLIB lookup itself — a
    lyrics miss/failure never blocks acquisition either way (§48)."""
    row = await get_app_settings(session)
    row.lyrics_enabled = enabled
    await session.commit()
    await session.refresh(row)
    return row


async def set_monitor_better_versions_enabled(session: AsyncSession, enabled: bool) -> AppSettings:
    """§36: the toggle is deliberately a plain on/off with no "quality-only"
    nuance — see `app.db.models.settings.MONITOR_BETTER_VERSIONS_WARNING` for
    the exact operator-facing warning the API surfaces alongside it.
    """
    row = await get_app_settings(session)
    row.monitor_better_versions_enabled = enabled
    await session.commit()
    await session.refresh(row)
    return row


# --- Phase 8: Jellyfin/Plex server configuration -----------------------------


@dataclass(frozen=True)
class JellyfinCredentials:
    url: str
    api_key: str
    user_id: str


async def set_jellyfin_config(
    session: AsyncSession,
    *,
    enabled: bool,
    url: str | None,
    api_key: str | None,
    user_id: str | None,
    library_id: str | None,
    media_path: str | None = None,
) -> AppSettings:
    row = await get_app_settings(session)
    row.jellyfin_enabled = enabled
    if url is not None:
        row.jellyfin_url = url
    if api_key:
        row.jellyfin_api_key_encrypted = encrypt_secret(api_key)
    if user_id is not None:
        row.jellyfin_user_id = user_id
    if library_id is not None:
        row.jellyfin_library_id = library_id
    if media_path is not None:
        row.jellyfin_media_path = media_path
    await session.commit()
    await session.refresh(row)
    return row


@dataclass(frozen=True)
class JellyfinConnection:
    url: str
    api_key: str


async def get_jellyfin_connection(session: AsyncSession) -> JellyfinConnection:
    """Just url + api key — enough to test the connection or list users, but
    NOT enough to mutate playlists (see `get_jellyfin_credentials` below).
    Raises `ValueError` (safe to surface as a 4xx) if not yet configured.
    """
    row = await get_app_settings(session)
    if not row.jellyfin_url or not row.jellyfin_api_key_encrypted:
        raise ValueError("Jellyfin server URL and API key are not configured.")
    return JellyfinConnection(url=row.jellyfin_url, api_key=decrypt_secret(row.jellyfin_api_key_encrypted))


async def get_jellyfin_credentials(session: AsyncSession) -> JellyfinCredentials:
    """Raises `ValueError` (safe to surface as a 4xx) if Jellyfin isn't fully
    configured yet — url + api key + a selected user are all required for
    the playlist-mutation calls that need a real userId (§2 row F).
    """
    connection = await get_jellyfin_connection(session)
    row = await get_app_settings(session)
    if not row.jellyfin_user_id:
        raise ValueError(
            "No Jellyfin user selected. Fetch the user list (GET /api/jellyfin/users) and "
            "set one via PUT /api/settings/jellyfin before syncing playlists."
        )
    return JellyfinCredentials(url=connection.url, api_key=connection.api_key, user_id=row.jellyfin_user_id)


@dataclass(frozen=True)
class PlexCredentials:
    url: str
    token: str


async def set_plex_config(
    session: AsyncSession,
    *,
    enabled: bool,
    url: str | None,
    token: str | None,
    library_section_id: str | None,
    media_path: str | None,
) -> AppSettings:
    row = await get_app_settings(session)
    row.plex_enabled = enabled
    if url is not None:
        row.plex_url = url
    if token:
        row.plex_token_encrypted = encrypt_secret(token)
    if library_section_id is not None:
        row.plex_library_section_id = library_section_id
    if media_path is not None:
        row.plex_media_path = media_path
    await session.commit()
    await session.refresh(row)
    return row


async def get_plex_credentials(session: AsyncSession) -> PlexCredentials:
    row = await get_app_settings(session)
    if not row.plex_url or not row.plex_token_encrypted:
        raise ValueError("Plex server URL and token are not configured.")
    return PlexCredentials(url=row.plex_url, token=decrypt_secret(row.plex_token_encrypted))
