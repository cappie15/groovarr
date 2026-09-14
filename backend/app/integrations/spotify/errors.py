"""Typed exceptions for the Spotify integration.

Callers (the sync job, the API layer) branch on these rather than parsing
HTTP status codes themselves, and each carries enough context to produce a
useful log line / API error without leaking upstream response bodies.
"""


class SpotifyError(Exception):
    """Base class for all Spotify-integration errors."""


class SpotifyAuthError(SpotifyError):
    """Client Credentials or PKCE token acquisition/refresh failed."""


class SpotifyReauthRequiredError(SpotifyAuthError):
    """The stored PKCE refresh token is no longer valid (e.g. Spotify's
    6-month hard expiry) — the user must go through "Connect your Spotify
    account" again. Distinct from a generic SpotifyAuthError so the API layer
    can surface a specific, actionable message.
    """


class SpotifyNotFoundError(SpotifyError):
    """The playlist/track ID does not exist (HTTP 404)."""


class SpotifyAccessDeniedError(SpotifyError):
    """The playlist exists but isn't readable with the credentials used —
    typically a private/collaborative playlist accessed via the default
    Client Credentials Flow, which can only read public/unlisted playlists.
    """


class SpotifyRateLimitedError(SpotifyError):
    """HTTP 429 — carries the Retry-After value (seconds) when present."""

    def __init__(self, message: str, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after
