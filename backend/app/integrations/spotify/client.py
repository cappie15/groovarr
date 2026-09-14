"""Spotify Web API client.

Default mode: Client Credentials Flow (app-only auth) — no user login,
sufficient for any public/unlisted playlist by ID (§2-E). PKCE user-token
exchange/refresh live here too, but are only ever invoked by the optional
"Connect your Spotify account" feature (app/api/spotify_oauth.py) and the
sync job's fallback path for playlists that come back inaccessible via
app-only auth.
"""

import time
from typing import Any

import httpx

from app.integrations.spotify.errors import (
    SpotifyAccessDeniedError,
    SpotifyAuthError,
    SpotifyNotFoundError,
    SpotifyRateLimitedError,
    SpotifyReauthRequiredError,
)

TOKEN_URL = "https://accounts.spotify.com/api/token"
API_BASE = "https://api.spotify.com/v1"

# Refresh the cached app token a bit before its stated expiry to avoid a
# request racing a just-expired token.
_TOKEN_EXPIRY_SAFETY_MARGIN_S = 60

# In-memory cache for the Client Credentials app token, keyed by client_id so
# changing credentials in Settings naturally invalidates the old entry
# instead of silently continuing to use a token minted for a different app.
_app_token_cache: dict[str, tuple[str, float]] = {}


def _clear_app_token_cache() -> None:
    """Test-only escape hatch — production code never needs this since the
    cache is correctly keyed/expired on its own.
    """
    _app_token_cache.clear()


class SpotifyClient:
    """Thin async wrapper around the subset of the Spotify Web API Groovarr
    needs. Takes an `httpx.AsyncClient` so tests can inject a mocked
    transport instead of hitting the network.
    """

    def __init__(self, http: httpx.AsyncClient) -> None:
        self._http = http

    # -- Client Credentials Flow (default, no-login) -------------------------

    async def get_app_access_token(self, client_id: str, client_secret: str) -> str:
        cached = _app_token_cache.get(client_id)
        if cached and cached[1] > time.monotonic():
            return cached[0]

        response = await self._http.post(
            TOKEN_URL,
            data={"grant_type": "client_credentials"},
            auth=(client_id, client_secret),
        )
        if response.status_code == 400:
            raise SpotifyAuthError(
                "Spotify rejected the Client Credentials request — "
                "check the configured Spotify Client ID/Secret."
            )
        response.raise_for_status()
        body = response.json()
        access_token: str = body["access_token"]
        expires_in = int(body.get("expires_in", 3600))
        _app_token_cache[client_id] = (
            access_token,
            time.monotonic() + expires_in - _TOKEN_EXPIRY_SAFETY_MARGIN_S,
        )
        return access_token

    # -- Authorization Code + PKCE (optional, private playlists) --------------

    async def exchange_pkce_code(
        self, *, client_id: str, code: str, redirect_uri: str, code_verifier: str
    ) -> dict[str, Any]:
        response = await self._http.post(
            TOKEN_URL,
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri,
                "client_id": client_id,
                "code_verifier": code_verifier,
            },
        )
        if response.status_code != 200:
            raise SpotifyAuthError(f"Spotify PKCE code exchange failed (HTTP {response.status_code})")
        result: dict[str, Any] = response.json()
        return result

    async def refresh_pkce_token(self, *, client_id: str, refresh_token: str) -> dict[str, Any]:
        response = await self._http.post(
            TOKEN_URL,
            data={
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": client_id,
            },
        )
        if response.status_code == 400:
            body = response.json() if response.content else {}
            if body.get("error") == "invalid_grant":
                raise SpotifyReauthRequiredError(
                    "Spotify refused to refresh the stored token (invalid_grant) — "
                    "the user must reconnect their Spotify account."
                )
            raise SpotifyAuthError(f"Spotify refresh_token request failed: {body}")
        response.raise_for_status()
        result: dict[str, Any] = response.json()
        return result

    # -- Playlist reads ---------------------------------------------------------

    async def get_playlist(self, playlist_id: str, access_token: str) -> dict[str, Any]:
        response = await self._http.get(
            f"{API_BASE}/playlists/{playlist_id}",
            params={"fields": "id,name,snapshot_id,images,public,collaborative"},
            headers=_auth_header(access_token),
        )
        return _raise_for_playlist_response(response, playlist_id)

    async def get_playlist_tracks(self, playlist_id: str, access_token: str) -> list[dict[str, Any]]:
        """Fetch every playlist item, following pagination. Each item is the
        raw Spotify "playlist item object" (has `.item`, a nested track
        object) — normalization into a `Track` row happens in the sync
        service, not here.

        LIVE-VERIFIED (2026-09-14): the legacy `/playlists/{id}/tracks`
        endpoint now returns a hard 403 regardless of token type — confirmed
        even with a genuine, freshly-refreshed PKCE user access token, not
        just Client Credentials. Its documented replacement,
        `/playlists/{id}/items`, works correctly and returns the same
        underlying track data, just nested one level deeper (`item.item`
        rather than `item.track`) — see `app/services/spotify_sync.py` for
        where that shape difference is consumed.
        """
        items: list[dict[str, Any]] = []
        url: str | None = f"{API_BASE}/playlists/{playlist_id}/items"
        params: dict[str, Any] | None = {"limit": 100}

        while url:
            response = await self._http.get(url, params=params, headers=_auth_header(access_token))
            data = _raise_for_playlist_response(response, playlist_id)
            items.extend(data.get("items", []))
            url = data.get("next")
            params = None  # `next` is already a fully-qualified URL with its own query string.

        return items


def _auth_header(access_token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {access_token}"}


def _raise_for_playlist_response(response: httpx.Response, playlist_id: str) -> dict[str, Any]:
    if response.status_code == 404:
        raise SpotifyNotFoundError(f"Spotify playlist {playlist_id!r} does not exist")
    if response.status_code in (401, 403):
        raise SpotifyAccessDeniedError(
            f"Spotify denied this request for playlist {playlist_id!r} "
            f"(HTTP {response.status_code}). This is not limited to private/collaborative "
            "playlists or to app-only (Client Credentials) tokens — Spotify's current API can "
            "deny this even for a genuinely public playlist and a valid user token, most often "
            "for the track-listing endpoint specifically."
        )
    if response.status_code == 429:
        retry_after = response.headers.get("Retry-After")
        raise SpotifyRateLimitedError(
            "Spotify rate-limited this request",
            retry_after=float(retry_after) if retry_after else None,
        )
    response.raise_for_status()
    result: dict[str, Any] = response.json()
    return result
