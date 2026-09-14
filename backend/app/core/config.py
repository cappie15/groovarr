"""Application settings, loaded from environment variables (and an optional
.env file for local development) via pydantic-settings.

Only the fields needed by Phase 1 (Foundation) are defined here. Later phases
add settings for Spotify/YouTube/Jellyfin/Plex behavior as those integrations
are built — see docs/00-research-and-architecture-review.md §11.
"""

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# backend/app/core/config.py -> parents[2] = backend/ -> parents[3] = repo
# root (dev layout: /opt/dev/groovarr) or /app (container layout, per
# docker/Dockerfile: WORKDIR /app, backend copied to ./backend, frontend
# build copied to ./frontend/dist) — both layouts put frontend/dist as a
# sibling of backend/, so this resolves correctly in either without relying
# on the process's current working directory.
_DEFAULT_FRONTEND_DIST_DIR = Path(__file__).resolve().parents[3] / "frontend" / "dist"


class Settings(BaseSettings):
    """Runtime configuration for the Groovarr backend.

    Field names map to environment variables of the same name, uppercased
    (e.g. `config_dir` <- `CONFIG_DIR`), which is pydantic-settings' default
    behavior — no explicit aliases needed.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Filesystem layout — matches the container's bind-mount conventions.
    config_dir: Path = Field(default=Path("/config"))
    media_dir: Path = Field(default=Path("/music-videos"))
    downloads_dir: Path = Field(default=Path("/downloads"))

    # HTTP server.
    port: int = 8080

    # Logging.
    log_level: str = "INFO"

    # Secret used to derive the at-rest encryption key for stored credentials
    # (Spotify refresh token, Jellyfin API key, Plex token) — see §9. Required:
    # the app should refuse to start without it rather than silently running
    # unencrypted secret storage later.
    groovarr_secret_key: str

    # Spotify (PKCE) — unused placeholders until Phase 2.
    spotify_client_id: str | None = None
    spotify_client_secret: str | None = None

    # YouTube Data API v3 — unused placeholder until Phase 4.
    youtube_api_key: str | None = None

    # Built frontend SPA (frontend/dist) served by this same process — the
    # project's single-process, single-container deployment model (§4/§5).
    # Defaults to the sibling frontend/dist directory in both the dev repo
    # layout and the container layout; override only if that ever changes.
    frontend_dist_dir: Path = Field(default=_DEFAULT_FRONTEND_DIST_DIR)


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide Settings instance, loaded once and cached.

    Using a cached accessor (rather than a module-level singleton evaluated at
    import time) keeps importing this module side-effect-free and makes it
    straightforward to override in tests via `get_settings.cache_clear()`.
    """
    return Settings()
