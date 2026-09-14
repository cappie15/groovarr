"""Spotify integration: Client Credentials Flow (default, no-login) plus an
optional Authorization Code + PKCE flow for private/collaborative playlists.
See docs/00-research-and-architecture-review.md §2-E / §3 / §7.
"""

from app.integrations.spotify.client import SpotifyClient
from app.integrations.spotify.errors import (
    SpotifyAccessDeniedError,
    SpotifyAuthError,
    SpotifyError,
    SpotifyNotFoundError,
    SpotifyRateLimitedError,
    SpotifyReauthRequiredError,
)

__all__ = [
    "SpotifyClient",
    "SpotifyError",
    "SpotifyAuthError",
    "SpotifyReauthRequiredError",
    "SpotifyNotFoundError",
    "SpotifyAccessDeniedError",
    "SpotifyRateLimitedError",
]
