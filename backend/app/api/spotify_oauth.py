"""Optional "Connect your Spotify account" (Authorization Code + PKCE) flow —
only usable once `spotify_user_oauth_enabled` is turned on in Settings.
Reading public/unlisted playlists (the default path) never touches this
router at all (§2-E).

The redirect target for the one-time interactive authorization step is a
route on Groovarr's own already-running API, not a throwaway local listener
(see app/integrations/spotify/pkce.py's module docstring for why).
"""

import secrets
import time

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_http_client
from app.db.session import get_session
from app.integrations.spotify.client import SpotifyClient
from app.integrations.spotify.pkce import build_authorize_url, generate_code_verifier
from app.services.settings_service import (
    get_app_settings,
    get_effective_spotify_credentials,
    store_spotify_user_refresh_token,
)

router = APIRouter(prefix="/api/spotify/oauth", tags=["spotify-oauth"])

# Short-lived, in-memory store for the PKCE code_verifier keyed by `state`,
# bridging /authorize and /callback. A single-user app doesn't need anything
# more durable than this — an unused entry simply expires.
_PENDING_TTL_S = 600
_pending: dict[str, tuple[str, float]] = {}


def _redirect_uri(request: Request) -> str:
    return str(request.url_for("spotify_oauth_callback"))


def _evict_expired_pending() -> None:
    now = time.monotonic()
    for state in [s for s, (_, expires_at) in _pending.items() if expires_at <= now]:
        _pending.pop(state, None)


@router.get("/authorize")
async def authorize(
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> RedirectResponse:
    settings_row = await get_app_settings(session)
    if not settings_row.spotify_user_oauth_enabled:
        raise HTTPException(
            status_code=409,
            detail='Enable "Connect your Spotify account" in Settings before starting this flow.',
        )

    try:
        creds = await get_effective_spotify_credentials(session)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    _evict_expired_pending()
    state = secrets.token_urlsafe(24)
    code_verifier = generate_code_verifier()
    _pending[state] = (code_verifier, time.monotonic() + _PENDING_TTL_S)

    url = build_authorize_url(
        client_id=creds.client_id,
        redirect_uri=_redirect_uri(request),
        state=state,
        code_verifier=code_verifier,
    )
    return RedirectResponse(url)


@router.get("/callback", name="spotify_oauth_callback")
async def callback(
    request: Request,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    session: AsyncSession = Depends(get_session),
    http: httpx.AsyncClient = Depends(get_http_client),
) -> HTMLResponse:
    if error:
        return HTMLResponse(f"<p>Spotify authorization failed: {error}</p>", status_code=400)
    if not code or not state or state not in _pending:
        raise HTTPException(status_code=400, detail="Missing or expired authorization state")

    code_verifier, _ = _pending.pop(state)

    try:
        creds = await get_effective_spotify_credentials(session)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    client = SpotifyClient(http)
    token_response = await client.exchange_pkce_code(
        client_id=creds.client_id,
        code=code,
        redirect_uri=_redirect_uri(request),
        code_verifier=code_verifier,
    )
    refresh_token = token_response.get("refresh_token")
    if not refresh_token:
        raise HTTPException(status_code=502, detail="Spotify did not return a refresh_token")

    await store_spotify_user_refresh_token(session, refresh_token)
    return HTMLResponse("<p>Spotify account connected. You can close this tab and return to Groovarr.</p>")
